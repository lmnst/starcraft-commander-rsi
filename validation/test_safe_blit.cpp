#include "../app/SafeBlit.h"
#include <cassert>
#include <cstdio>
#include <vector>
#include <random>
using Blit=BOOL (WINAPI*)(void*,void*,int,int,int,int,int,DWORD);
int main() {
    auto storm=LoadLibraryA("E:\\StarCraftAI\\StarCraft1161\\storm.dll");
    auto original=reinterpret_cast<Blit>(GetProcAddress(storm,MAKEINTRESOURCEA(313)));assert(original);
    std::mt19937 random(721);
    std::vector<unsigned char> source(65536),expected(65536),actual(65536);
    for(auto& value:source)value=static_cast<unsigned char>(random());
    for(int i=0;i<20000;++i) {
        int width=int(random()%801),height=int(random()%40),sourcePitch=width+int(random()%32),destPitch=800;
        int sourceOffset=int(random()%8),destOffset=int(random()%8);
        std::fill(expected.begin(),expected.end(),213);actual=expected;
        assert(original(expected.data()+destOffset,source.data()+sourceOffset,width,height,destPitch,sourcePitch,0,0x00CC0020));
        assert(SafeBlit::copy(actual.data()+destOffset,source.data()+sourceOffset,width,height,destPitch,sourcePitch));
        assert(expected==actual);
    }
    auto guarded=static_cast<unsigned char*>(VirtualAlloc(nullptr,8192,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    DWORD old=0;VirtualProtect(guarded+4096,4096,PAGE_NOACCESS,&old);
    assert(SafeBlit::copy(actual.data(),guarded+4095,1,1,800,1));
    assert(!SafeBlit::copy(actual.data(),guarded+4095,2,1,800,2));
    assert(!SafeBlit::copy(guarded+4095,source.data(),2,1,2,2));
    assert(!SafeBlit::copy(actual.data(),source.data(),20,10,19,20));
    assert(!SafeBlit::copy(actual.data(),source.data(),20,10,800,19));
    assert(!SafeBlit::copy(actual.data(),source.data(),1,5,0x7fffffff,1));
    std::puts("20000 Storm pixel comparisons and guard-page boundary checks passed");
}
