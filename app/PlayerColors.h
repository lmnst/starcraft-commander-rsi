#pragma once
#include <windows.h>
#include <BWAPI.h>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

// Local presentation only: do not alter maps, replay commands, simulation,
// alliances, player ownership, or any frozen/opponent binary.
namespace PlayerColors {
inline constexpr const char* policy="self_blue_enemy_red_v1";
inline bool applied=false;
inline int selfId=-1;
inline const char* failure="";
inline std::array<int,5> sourceFormat{};
inline std::array<std::array<std::uint8_t,8>,2> ramps{};
inline int word(const std::vector<std::uint8_t>& b,size_t n) {return b[n]|(int(b[n+1])<<8);}
inline bool decodeRamps(const std::vector<std::uint8_t>& b,std::array<std::array<std::uint8_t,8>,2>& out) {
    if(b.size()<128 || b[0]!=10 || b[2]!=1 || b[3]!=8 || b[65]!=1)return false;
    int width=word(b,8)-word(b,4)+1,height=word(b,10)-word(b,6)+1,stride=word(b,66);
    if(width<8 || width>1024 || height<1 || width*height<16 || stride<width || stride>2048)return false;
    size_t source=128,decoded=0,visible=0,total=size_t(stride)*height;
    while(decoded<total && visible<16) {
        if(source>=b.size())return false;
        unsigned value=b[source++],count=1;
        if((value&0xc0)==0xc0) {
            count=value&63;if(!count || source>=b.size())return false;
            value=b[source++];
        }
        if(decoded+count>size_t(stride)*height)return false;
        for(unsigned n=0;n<count && decoded<total;++n,++decoded)
            if(decoded%stride<size_t(width) && visible<16){out[visible/8][visible%8]=std::uint8_t(value);++visible;}
    }
    return out[0]!=out[1];
}
inline bool loadRamps() {
    HMODULE storm=GetModuleHandleW(L"Storm.dll");if(!storm){failure="storm_missing";return false;}
    auto open=reinterpret_cast<BOOL(WINAPI*)(const char*,HANDLE*)>(GetProcAddress(storm,MAKEINTRESOURCEA(267)));
    auto size=reinterpret_cast<DWORD(WINAPI*)(HANDLE,DWORD*)>(GetProcAddress(storm,MAKEINTRESOURCEA(265)));
    auto read=reinterpret_cast<BOOL(WINAPI*)(HANDLE,void*,DWORD,DWORD*,void*)>(GetProcAddress(storm,MAKEINTRESOURCEA(269)));
    auto close=reinterpret_cast<BOOL(WINAPI*)(HANDLE)>(GetProcAddress(storm,MAKEINTRESOURCEA(253)));
    if(!open || !size || !read || !close){failure="storm_exports_missing";return false;}
    HANDLE file=nullptr;if(!open("game\\tunit.pcx",&file)){failure="color_asset_missing";return false;}
    DWORD length=size(file,nullptr),got=0;
    if(length<128 || length>1024*1024){close(file);failure="color_asset_size";return false;}
    std::vector<std::uint8_t> bytes(length);
    bool ok=read(file,bytes.data(),length,&got,nullptr)!=FALSE;close(file);
    if(!ok || got!=length){failure="color_asset_read";return false;}
    sourceFormat={int(bytes[2]),int(bytes[3]),word(bytes,8)-word(bytes,4)+1,word(bytes,10)-word(bytes,6)+1,int(bytes[65])};
    if(!decodeRamps(bytes,ramps)){failure="color_asset_decode";return false;}
    return true;
}
inline bool apply(BWAPI::Game* game,int originalSelf=-1) {
    applied=false;
    const char* setting=std::getenv("COMMANDER_COLOR_POLICY");
    if(!setting || std::strcmp(setting,policy)!=0)return false;
    selfId=game->isReplay()?originalSelf:game->self()?game->self()->getID():-1;
    if(selfId<0 || selfId>=8 || !loadRamps()) {
        std::ofstream out("bwapi-data/write/player-colors.json",std::ios::trunc);
        out<<"{\"applied\":false,\"error\":\""<<failure<<"\",\"self_player_id\":"<<selfId<<",\"source_format\":[";
        for(size_t i=0;i<sourceFormat.size();++i){if(i)out<<',';out<<sourceFormat[i];}out<<"]}\n";
        return false;
    }
    // BWAPI 4.4's BWGame at 0x57F0F0: tunitImagePalette (0x2c86),
    // twelve 8-byte sprite remaps; playerColorIndex (0x2ce6), minimap colors.
    // The remaps come from the running game's own assets, not guessed shades.
    auto sprites=reinterpret_cast<volatile std::uint8_t*>(0x00581d76);
    auto minimap=reinterpret_cast<volatile std::uint8_t*>(0x00581dd6);
    bool ours=false,enemy=false;
    for(auto p:game->getPlayers()) {
        int id=p->getID();auto type=p->getType();
        if(id<0 || id>=8 || p->isNeutral() || p->isObserver() ||
            (type!=BWAPI::PlayerTypes::Player && type!=BWAPI::PlayerTypes::Computer &&
             type!=BWAPI::PlayerTypes::PlayerLeft && type!=BWAPI::PlayerTypes::ComputerLeft))continue;
        int row=id==selfId?1:0;
        for(int i=0;i<8;++i)sprites[id*8+i]=ramps[row][i];
        minimap[id]=std::uint8_t(id==selfId?BWAPI::Colors::Blue.getID():BWAPI::Colors::Red.getID());
        ours|=id==selfId;enemy|=id!=selfId;
    }
    applied=ours && enemy;
    std::ofstream out("bwapi-data/write/player-colors.json",std::ios::trunc);
    out<<"{\"policy\":\""<<policy<<"\",\"applied\":"<<(applied?"true":"false")<<",\"self_player_id\":"<<selfId<<",\"players\":[";
    bool comma=false;
    for(auto p:game->getPlayers())if(!p->isNeutral() && !p->isObserver() && p->getID()>=0 && p->getID()<8) {
        int id=p->getID();if(comma)out<<',';comma=true;
        out<<"{\"id\":"<<id<<",\"minimap\":"<<int(minimap[id])<<",\"sprite_remap\":[";
        for(int i=0;i<8;++i){if(i)out<<',';out<<int(sprites[id*8+i]);}out<<"]}";
    }
    out<<"]}\n";
    return applied;
}
inline BWAPI::Color actualColor(BWAPI::Player player) {
    return applied && player->getID()>=0 && player->getID()<8?
        BWAPI::Color(reinterpret_cast<volatile std::uint8_t*>(0x00581dd6)[player->getID()]):player->getColor();
}
}
