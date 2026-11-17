#pragma once
#include <windows.h>
#include <BWAPI.h>
#include <array>
#include <map>
#include <memory>
#include <utility>
#include <vector>

// A per-object API gate, never a patch to Baseline.dll or BWAPI code pages.
// Only issueCommand is replaced on owned Probes. Queries and economy/build
// commands still use the original vtable. All tables are restored on reset/end.
class ProbeCommandGuard {
public:
    using Filter=bool(*)(void*,BWAPI::Unit,const BWAPI::UnitCommand&);
private:
    using Original=bool(__thiscall*)(BWAPI::Unit,BWAPI::UnitCommand);
    struct Entry {void** original=nullptr;std::vector<void*> table;void* owner=nullptr;Filter filter=nullptr;};
    inline static std::map<BWAPI::Unit,std::unique_ptr<Entry>> entries;
    inline static size_t detected=1024;
    template<size_t N> static bool __fastcall detect(BWAPI::Unit,void*,BWAPI::UnitCommand) {detected=N;return false;}
    template<size_t... N> static auto detectors(std::index_sequence<N...>) {
        return std::array<void*,sizeof...(N)>{reinterpret_cast<void*>(&detect<N>)...};
    }
    __declspec(noinline) static void invoke(BWAPI::Unit unit) {unit->issueCommand(BWAPI::UnitCommand{});}
    static size_t slot() {
        static size_t result=[] {
            auto table=detectors(std::make_index_sequence<512>{});
            struct Fake {void** vptr;} fake{table.data()};
            invoke(reinterpret_cast<BWAPI::Unit>(&fake));return detected;
        }();return result;
    }
    static bool executable(void* p) {
        MEMORY_BASIC_INFORMATION info{};
        if(!VirtualQuery(p,&info,sizeof(info)) || info.State!=MEM_COMMIT || (info.Protect&(PAGE_GUARD|PAGE_NOACCESS)))return false;
        return (info.Protect&(PAGE_EXECUTE|PAGE_EXECUTE_READ|PAGE_EXECUTE_READWRITE|PAGE_EXECUTE_WRITECOPY))!=0;
    }
    static bool __fastcall dispatch(BWAPI::Unit unit,void*,BWAPI::UnitCommand command) {
        auto found=entries.find(unit);if(found==entries.end())return false;
        auto& e=*found->second;
        if(e.filter && !e.filter(e.owner,unit,command))return false;
        return reinterpret_cast<Original>(e.original[slot()])(unit,command);
    }
public:
    static bool attach(BWAPI::Unit unit,void* owner,Filter filter) {
        static_assert(sizeof(void*)==4,"The native SC 1.16.1 adapter is x86.");
        if(entries.count(unit))return true;
        size_t index=slot();if(index>=512)return false;
        auto e=std::make_unique<Entry>();e->original=*reinterpret_cast<void***>(unit);
        size_t count=0;
        while(count<512) {
            MEMORY_BASIC_INFORMATION region{};
            if(!VirtualQuery(e->original+count,&region,sizeof(region)) || region.State!=MEM_COMMIT ||
               (region.Protect&(PAGE_GUARD|PAGE_NOACCESS)) || !executable(e->original[count]))break;
            ++count;
        }
        if(index>=count)return false;
        // Preserve MSVC's RTTI locator immediately before the table as well.
        e->table.assign(e->original-1,e->original+count);
        e->table[index+1]=reinterpret_cast<void*>(&dispatch);e->owner=owner;e->filter=filter;
        auto replacement=e->table.data()+1;
        entries.emplace(unit,std::move(e));*reinterpret_cast<void***>(unit)=replacement;
        return true;
    }
    static void release(BWAPI::Unit unit) {
        auto found=entries.find(unit);if(found==entries.end())return;
        *reinterpret_cast<void***>(unit)=found->second->original;entries.erase(found);
    }
    static void releaseAll(void* owner) {
        for(auto it=entries.begin();it!=entries.end();) {
            if(it->second->owner!=owner){++it;continue;}
            *reinterpret_cast<void***>(it->first)=it->second->original;it=entries.erase(it);
        }
    }
    static size_t count(void* owner) {size_t n=0;for(const auto& e:entries)n+=e.second->owner==owner;return n;}
};
