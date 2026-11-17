#include "../vendor/local-pc-transport/SNP/SNPModule.h"
#include <thread>
#include <mutex>
#include <cstdio>
#include <cassert>
#include <atomic>
struct ContendedNetwork final:SNP::BinNetwork {
    std::mutex networkMutex;
    HANDLE sending=CreateEventA(nullptr,TRUE,FALSE,nullptr),forward=CreateEventA(nullptr,TRUE,FALSE,nullptr);
    void initialize() override {}
    void destroy() override {}
    void requestAds() override {SOCKADDR peer{};char payload[4]={1,2,3,4};sendAsyn(peer,Util::MemoryFrame(payload,4));}
    void receive() override {std::lock_guard<std::mutex> lock(networkMutex);}
    void sendAsyn(const SOCKADDR& peer,Util::MemoryFrame packet) override {
        std::lock_guard<std::mutex> lock(networkMutex);
        SetEvent(sending);WaitForSingleObject(forward,2000);
        passPacket(peer,packet); // Actual SNP callback acquires its internal critical section.
    }
    void startAdvertising(Util::MemoryFrame) override {}
    void stopAdvertising() override {}
};
int main() {
    ContendedNetwork network;SNP::pluggedNetwork=&network;
    client_info info{};HANDLE event=CreateEventA(nullptr,FALSE,FALSE,nullptr);
    assert(SNP::spiFunctions.spiInitialize(&info,nullptr,nullptr,nullptr,event));
    HANDLE sent=CreateEventA(nullptr,TRUE,FALSE,nullptr),received=CreateEventA(nullptr,TRUE,FALSE,nullptr);
    SOCKADDR peer{};SOCKADDR* peers[]={&peer};char payload[4]={1,2,3,4};
    std::thread sender([&]{assert(SNP::spiFunctions.spiSend(1,peers,payload,4));SetEvent(sent);});
    assert(WaitForSingleObject(network.sending,2000)==WAIT_OBJECT_0);
    std::thread receiver([&]{SOCKADDR* addr=nullptr;char* data=nullptr;DWORD size=0;
        if(SNP::spiFunctions.spiReceive(&addr,&data,&size)) {assert(size==4);SNP::spiFunctions.spiFree(addr,data,size);}SetEvent(received);});
    // Force receive to race with a send callback holding the network mutex.
    Sleep(100);SetEvent(network.forward);
    HANDLE done[]={sent,received};
    if(WaitForMultipleObjects(2,done,TRUE,2500)!=WAIT_OBJECT_0) {
        std::puts("FAILED: actual SNP send/receive lock inversion deadlocked");std::fflush(stdout);ExitProcess(3);
    }
    sender.join();receiver.join();std::puts("PASSED: actual SNP send/receive callbacks complete under forced contention");
    ResetEvent(sent);ResetEvent(received);ResetEvent(network.sending);ResetEvent(network.forward);
    std::thread lobby([&]{game* list=nullptr;DWORD unused=0;
        assert(reinterpret_cast<BOOL(__stdcall*)(int,int,game**)>(SNP::spiFunctions.spiLockGameList)(0,0,&list));
        assert(reinterpret_cast<BOOL(__stdcall*)(game*,DWORD*)>(SNP::spiFunctions.spiUnlockGameList)(list,&unused));SetEvent(sent);});
    assert(WaitForSingleObject(network.sending,2000)==WAIT_OBJECT_0);
    std::thread lobbyReceive([&]{SOCKADDR* addr=nullptr;char* data=nullptr;DWORD size=0;
        if(SNP::spiFunctions.spiReceive(&addr,&data,&size))SNP::spiFunctions.spiFree(addr,data,size);SetEvent(received);});
    Sleep(100);SetEvent(network.forward);
    if(WaitForMultipleObjects(2,done,TRUE,2500)!=WAIT_OBJECT_0) {
        std::puts("FAILED: actual SNP lobby refresh/receive lock inversion deadlocked");std::fflush(stdout);ExitProcess(5);
    }
    lobby.join();lobbyReceive.join();std::puts("PASSED: actual SNP lobby refresh/receive callbacks complete under forced contention");
    ResetEvent(sent);ResetEvent(received);std::atomic<int> consumed{0};
    std::thread producer([&]{for(int i=1;i<=10000;++i)assert(SNP::spiFunctions.spiSend(1,peers,reinterpret_cast<char*>(&i),sizeof(i)));SetEvent(sent);});
    std::thread consumer([&]{while(consumed<10000) {
        SOCKADDR* addr=nullptr;char* data=nullptr;DWORD size=0;
        if(SNP::spiFunctions.spiReceive(&addr,&data,&size)) {
            assert(size==sizeof(int) && *reinterpret_cast<int*>(data)==consumed+1);
            ++consumed;SNP::spiFunctions.spiFree(addr,data,size);
        } else SwitchToThread();
    }SetEvent(received);});
    if(WaitForMultipleObjects(2,done,TRUE,10000)!=WAIT_OBJECT_0) {
        std::puts("FAILED: concurrent packet flood did not complete");std::fflush(stdout);ExitProcess(4);
    }
    producer.join();consumer.join();assert(consumed==10000);
    std::puts("PASSED: 10000 concurrent packets delivered in exact FIFO order, no deadlock or loss");
    struct Advertisement {game info{};char extra[32]{};} ad;
    ad.extra[0]=17;SNP::passAdvertisement(peer,Util::MemoryFrame::from(ad));
    game* snapshot=nullptr;DWORD unused=0;
    assert(reinterpret_cast<BOOL(__stdcall*)(int,int,game**)>(SNP::spiFunctions.spiLockGameList)(0,0,&snapshot));
    assert(snapshot && static_cast<char*>(snapshot->pExtra)[0]==17);
    ad.extra[0]=99;SNP::passAdvertisement(peer,Util::MemoryFrame::from(ad));
    assert(static_cast<char*>(snapshot->pExtra)[0]==17); // Live refresh cannot mutate a loan.
    ResetEvent(sent);ResetEvent(received);
    std::thread duringListSend([&]{assert(SNP::spiFunctions.spiSend(1,peers,payload,4));SetEvent(sent);});
    std::thread duringListReceive([&]{SOCKADDR* addr=nullptr;char* data=nullptr;DWORD size=0;
        while(!SNP::spiFunctions.spiReceive(&addr,&data,&size))SwitchToThread();
        assert(size==4);SNP::spiFunctions.spiFree(addr,data,size);SetEvent(received);});
    if(WaitForMultipleObjects(2,done,TRUE,2500)!=WAIT_OBJECT_0) {
        std::puts("FAILED: holding a Storm game-list loan blocks packet delivery");std::fflush(stdout);ExitProcess(6);
    }
    duringListSend.join();duringListReceive.join();
    assert(reinterpret_cast<BOOL(__stdcall*)(game*,DWORD*)>(SNP::spiFunctions.spiUnlockGameList)(snapshot,&unused));
    std::puts("PASSED: stable advertisement snapshot held across concurrent packet delivery");
}
