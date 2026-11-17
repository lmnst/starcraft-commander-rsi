// Compatibility adapter for the pinned BWAPI-distributed Resolution Expander.
// Adapt module lookup and SRCCOPY rendering. Simulation and orders are untouched.
#include <windows.h>
#include <cstring>
#include <cstdio>
#include "SafeBlit.h"

static LONG faultRecorded = 0;
using Blit = BOOL (WINAPI*)(void*,void*,int,int,int,int,int,DWORD);
static Blit originalBlit=nullptr;
static volatile DWORD lastBlit[9]={};
static unsigned long long safeCopies=0,rejectedCopies=0;
static ULONGLONG lastCopyPublish=0;
static void publishCopies() {
    lastCopyPublish=GetTickCount64();
    char data[256]={};
    int length=sprintf_s(data,"{\"policy\":\"checked_srccopy_v1\",\"copies\":%llu,\"rejected\":%llu}\n",safeCopies,rejectedCopies);
    HANDLE file=CreateFileA("bwapi-data/write/wide-view-render.json",GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE,
                           nullptr,CREATE_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file!=INVALID_HANDLE_VALUE){DWORD written=0;WriteFile(file,data,length,&written,nullptr);CloseHandle(file);}
}
static BOOL WINAPI traceBlit(void* dest,void* source,int width,int height,int destPitch,int sourcePitch,int flags,DWORD rop) {
    lastBlit[0]=reinterpret_cast<DWORD>(dest);lastBlit[1]=reinterpret_cast<DWORD>(source);
    lastBlit[2]=width;lastBlit[3]=height;lastBlit[4]=destPitch;lastBlit[5]=sourcePitch;
    lastBlit[6]=flags;lastBlit[7]=rop;lastBlit[8]=1;
    BOOL result;
    if(flags==0 && rop==0x00CC0020) {
        result=SafeBlit::copy(dest,source,width,height,destPitch,sourcePitch);
        ++safeCopies;if(!result)++rejectedCopies;
        if(safeCopies==1 || !result || GetTickCount64()-lastCopyPublish>=1000)publishCopies();
    } else result=originalBlit(dest,source,width,height,destPitch,sourcePitch,flags,rop);
    lastBlit[8]=0;
    return result;
}
static LONG CALLBACK recordFault(EXCEPTION_POINTERS* fault) {
    if (fault->ExceptionRecord->ExceptionCode != EXCEPTION_ACCESS_VIOLATION ||
        InterlockedCompareExchange(&faultRecorded, 1, 0)) return EXCEPTION_CONTINUE_SEARCH;
    auto context=fault->ContextRecord;
    MEMORY_BASIC_INFORMATION region{};
    VirtualQuery(fault->ExceptionRecord->ExceptionAddress,&region,sizeof(region));
    unsigned char code[96]={}; DWORD stack[40]={}; SIZE_T got=0;
    ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(context->Eip-32),code,sizeof(code),&got);
    ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(context->Esp),stack,sizeof(stack),&got);
    char buffer[4096]={};int length=0;
    length+=sprintf_s(buffer+length,sizeof(buffer)-length,
        "{\"code\":%lu,\"ip\":%lu,\"access\":%lu,\"address\":%lu,\"allocation\":%lu,\"protection\":%lu,\"region_size\":%lu,\"registers\":[%lu,%lu,%lu,%lu,%lu,%lu],\"instructions\":\"",
        fault->ExceptionRecord->ExceptionCode,context->Eip,
        static_cast<DWORD>(fault->ExceptionRecord->ExceptionInformation[0]),
        static_cast<DWORD>(fault->ExceptionRecord->ExceptionInformation[1]),
        reinterpret_cast<DWORD>(region.AllocationBase),region.Protect,static_cast<DWORD>(region.RegionSize),
        context->Eax,context->Ebx,context->Ecx,context->Edx,context->Esi,context->Edi);
    for(auto byte:code)length+=sprintf_s(buffer+length,sizeof(buffer)-length,"%02x",byte);
    length+=sprintf_s(buffer+length,sizeof(buffer)-length,"\",\"stack\":[");
    for(int i=0;i<40;++i)length+=sprintf_s(buffer+length,sizeof(buffer)-length,"%s%lu",i?",":"",stack[i]);
    length+=sprintf_s(buffer+length,sizeof(buffer)-length,"],\"last_blit\":[");
    for(int i=0;i<9;++i)length+=sprintf_s(buffer+length,sizeof(buffer)-length,"%s%lu",i?",":"",lastBlit[i]);
    length+=sprintf_s(buffer+length,sizeof(buffer)-length,"]}\n");
    HANDLE file=CreateFileA("bwapi-data/write/wide-view-fault.jsonl",FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,
                           nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
    if(file!=INVALID_HANDLE_VALUE){DWORD written=0;WriteFile(file,buffer,length,&written,nullptr);CloseHandle(file);}
    return EXCEPTION_CONTINUE_SEARCH;
}

struct ModuleInfo { void* base; DWORD size; void* entry; };
static bool __cdecl ownModule(ModuleInfo* output, const char* name) {
    HMODULE module = GetModuleHandleA(name);
    if (!module || !output) return false;
    auto base = reinterpret_cast<BYTE*>(module);
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || nt->Signature != IMAGE_NT_SIGNATURE) return false;
    output->base = base;
    output->size = nt->OptionalHeader.SizeOfImage;
    output->entry = base + nt->OptionalHeader.AddressOfEntryPoint;
    return true;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    DisableThreadLibraryCalls(instance);
    HMODULE expander = LoadLibraryA("bwapi-data\\ResolutionExpander.dll");
    if (!expander) return FALSE;
    auto base = reinterpret_cast<BYTE*>(expander);
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (nt->OptionalHeader.SizeOfImage != 0x161000) return FALSE;
    // The old lookup opens the process found by its window title and then scans
    // a null module on modern Windows when that lookup fails. The expander is
    // already inside StarCraft; use loaded modules without process permissions.
    auto target = base + 0x18580;
    const BYTE expected[] = {0x55, 0x8b, 0xec, 0x81, 0xec, 0x44, 0x01, 0x00, 0x00};
    if (std::memcmp(target, expected, sizeof(expected))) return FALSE;
    DWORD old = 0;
    if (!VirtualProtect(target, 5, PAGE_EXECUTE_READWRITE, &old)) return FALSE;
    target[0] = 0xe9;
    *reinterpret_cast<DWORD*>(target+1) = reinterpret_cast<DWORD>(&ownModule) - reinterpret_cast<DWORD>(target) - 5;
    FlushInstructionCache(GetCurrentProcess(), target, 5);
    VirtualProtect(target, 5, old, &old);
    AddVectoredExceptionHandler(1,recordFault);
    auto blitSlot=reinterpret_cast<void**>(0x004FE3B8);
    auto expectedBlit=GetProcAddress(GetModuleHandleA("storm.dll"),MAKEINTRESOURCEA(313));
    if(*blitSlot==reinterpret_cast<void*>(expectedBlit) && VirtualProtect(blitSlot,sizeof(void*),PAGE_READWRITE,&old)) {
        originalBlit=reinterpret_cast<Blit>(*blitSlot);
        *blitSlot=reinterpret_cast<void*>(&traceBlit);
        VirtualProtect(blitSlot,sizeof(void*),old,&old);
    }
    return TRUE;
}
