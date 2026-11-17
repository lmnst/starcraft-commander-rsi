#pragma once
#include <windows.h>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace SafeBlit {
inline bool accessible(const void* pointer,std::size_t bytes,bool write) {
    auto position=reinterpret_cast<std::uintptr_t>(pointer);
    if(!position || bytes>32u*1024u*1024u || position+bytes<position)return false;
    const auto end=position+bytes;
    while(position<end) {
        MEMORY_BASIC_INFORMATION info{};
        if(!VirtualQuery(reinterpret_cast<void*>(position),&info,sizeof(info)) || info.State!=MEM_COMMIT ||
           info.Protect&(PAGE_GUARD|PAGE_NOACCESS))return false;
        DWORD access=info.Protect&0xff;
        bool writable=access==PAGE_READWRITE || access==PAGE_WRITECOPY || access==PAGE_EXECUTE_READWRITE || access==PAGE_EXECUTE_WRITECOPY;
        if(write?!writable:!(writable || access==PAGE_READONLY || access==PAGE_EXECUTE_READ))return false;
        auto next=reinterpret_cast<std::uintptr_t>(info.BaseAddress)+info.RegionSize;
        if(next<=position)return false;
        position=next;
    }
    return true;
}
// Eight-bit SRCCOPY, the operation used by the native screen/cursor copy.
// Keep it ordinary code instead of executing Storm's generated blitter.
inline BOOL copy(void* dest,const void* source,int width,int height,int destPitch,int sourcePitch) {
    if(width<=0 || height<=0)return TRUE;
    if(destPitch<width || sourcePitch<width || height>16384)return FALSE;
    auto destSpan=std::uint64_t(height-1)*std::uint64_t(destPitch)+std::uint64_t(width);
    auto sourceSpan=std::uint64_t(height-1)*std::uint64_t(sourcePitch)+std::uint64_t(width);
    if(destSpan>32u*1024u*1024u || sourceSpan>32u*1024u*1024u)return FALSE;
    auto destBytes=std::size_t(destSpan),sourceBytes=std::size_t(sourceSpan);
    if(!accessible(dest,destBytes,true) || !accessible(source,sourceBytes,false))return FALSE;
    auto d=static_cast<unsigned char*>(dest);auto s=static_cast<const unsigned char*>(source);
    bool backward=reinterpret_cast<std::uintptr_t>(d)>reinterpret_cast<std::uintptr_t>(s) &&
                  reinterpret_cast<std::uintptr_t>(d)<reinterpret_cast<std::uintptr_t>(s)+sourceBytes;
    for(int row=0;row<height;++row) {
        int y=backward?height-1-row:row;
        std::memmove(d+std::size_t(y)*destPitch,s+std::size_t(y)*sourcePitch,std::size_t(width));
    }
    return TRUE;
}
}
