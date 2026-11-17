#include <windows.h>
#include <cstdio>
#include <cstring>
using Blit = BOOL (WINAPI*)(void*,void*,int,int,int,int,int,DWORD);
static int report(EXCEPTION_POINTERS* fault) {
    std::printf("FAULT code=%08lx ip=%p access=%lu address=%p\n",fault->ExceptionRecord->ExceptionCode,
        fault->ExceptionRecord->ExceptionAddress,fault->ExceptionRecord->ExceptionInformation[0],
        reinterpret_cast<void*>(fault->ExceptionRecord->ExceptionInformation[1]));
    return EXCEPTION_EXECUTE_HANDLER;
}
int main() {
    auto storm=LoadLibraryA("E:\\StarCraftAI\\StarCraft1161\\storm.dll");
    auto blit=reinterpret_cast<Blit>(GetProcAddress(storm,MAKEINTRESOURCEA(313)));
    if(!blit)return 2;
    auto dest=static_cast<unsigned char*>(VirtualAlloc(nullptr,1024*1024,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    auto source=static_cast<unsigned char*>(VirtualAlloc(nullptr,1024*1024,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    std::memset(source,17,1024*1024);
    for(int width=0;width<=800;++width)for(int height=0;height<=9;++height)for(int align=0;align<4;++align) {
        __try {blit(dest+align,source+align,width,height,800,800,0,0x00CC0020);}
        __except(report(GetExceptionInformation())) {std::printf("width=%d height=%d align=%d\n",width,height,align);return 1;}
    }
    std::puts("All width/height and alignment cases passed");
    for(int pitch=1;pitch<=64;++pitch)for(int width=1;width<=pitch;++width)for(int height=1;height<=32;++height) {
        __try {blit(dest+(height%4),source+(width%4),width,height,800,pitch,0,0x00CC0020);}
        __except(report(GetExceptionInformation())) {std::printf("width=%d height=%d sourcePitch=%d\n",width,height,pitch);return 1;}
    }
    std::puts("Cursor-like pitches and clipping passed");
    auto guardedSource=static_cast<unsigned char*>(VirtualAlloc(nullptr,8192,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    auto guardedDest=static_cast<unsigned char*>(VirtualAlloc(nullptr,65536,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE));
    DWORD old=0;VirtualProtect(guardedSource+4096,4096,PAGE_NOACCESS,&old);
    VirtualProtect(guardedDest+61440,4096,PAGE_NOACCESS,&old);
    for(int width=1;width<=64;++width)for(int height=1;height<=32;++height) {
        auto sourceEnd=guardedSource+4096-width*height;
        auto destEnd=guardedDest+61440-(height-1)*800-width;
        __try {blit(destEnd,sourceEnd,width,height,800,width,0,0x00CC0020);}
        __except(report(GetExceptionInformation())) {std::printf("Guard page width=%d height=%d\n",width,height);return 1;}
    }
    std::puts("Exact buffer boundaries passed");
}
