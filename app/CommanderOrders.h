#pragma once
#include <algorithm>
#include <set>
#include <vector>

namespace CommanderOrders {
enum class Scope { All, Squad };
enum class Action { Expansion, MainBase, Gather, Retreat, Defend, Clear };
inline const char* scopeName(Scope scope) { return scope==Scope::Squad?"squad":"all"; }
inline const char* actionName(Action action) {
    switch(action) {
        case Action::Expansion:return "expansion";
        case Action::MainBase:return "attack";
        case Action::Gather:return "rally";
        case Action::Retreat:return "retreat";
        case Action::Clear:return "clear_enemies";
        default:return "defend";
    }
}
inline int squadSize(int healthy, int eligible) {
    int count=std::min({std::max(2,healthy/4),healthy-6,eligible});
    return count>=2?count:0;
}
struct Candidate { int id; bool healthy, eligible; };
struct Rebalance {
    int target=0,healthy=0,reserveHealthy=0;
    std::set<int> added,returned,lost;
    bool changed() const {return !added.empty() || !returned.empty() || !lost.empty();}
};
struct Result {
    bool accepted=false;
    int task=0,reserveTask=0,inheritedTask=0;
    const char* status="not_enough_healthy_units";
    std::set<int> units,reserves,releasedTasks,manualUnits;
};

// Selecting ALL changes only the next action's scope. Entering SQUAD forms a
// group, preserves its current job and sends everyone else to base. Once formed,
// it automatically recruits/replaces toward a quarter, retaining six healthy
// reserves. Selecting ALL alone does not end the existing squad's task.
class Plan {
public:
    Scope selected=Scope::All;
    bool formed=false;
    int mainTask=0, squadTask=0, serial=0;
    std::set<int> squad;
    std::set<int> pendingLosses;
    void select(Scope scope) { selected=scope; }
    void prune(const std::set<int>& alive) {
        for(auto it=squad.begin();it!=squad.end();) {
            if(!alive.count(*it)){pendingLosses.insert(*it);it=squad.erase(it);}else ++it;
        }
    }
    int taskFor(int id) const { return squad.count(id)?squadTask:mainTask; }
    Rebalance reinforce(const std::vector<Candidate>& candidates) {
        Rebalance result;
        if(!formed)return result;
        std::set<int> alive;
        for(const auto& c:candidates){alive.insert(c.id);result.healthy+=c.healthy;}
        prune(alive);
        result.lost.swap(pendingLosses);
        // A formed squad can temporarily shrink to one/zero to protect home.
        // Initial creation still requires two members plus six reserves.
        result.target=std::max(0,std::min(std::max(2,result.healthy/4),result.healthy-6));
        int healthySquad=0;
        for(const auto& c:candidates)healthySquad+=c.healthy && squad.count(c.id);
        // Candidates are ranked nearest the existing objective first. Return
        // rear elements first; never reshuffle members merely because of rank.
        for(auto it=candidates.rbegin();it!=candidates.rend();++it) {
            bool needHealthy=result.healthy-healthySquad<std::min(6,result.healthy);
            if(!squad.count(it->id))continue;
            if(needHealthy ? !it->healthy : int(squad.size())<=result.target)continue;
            squad.erase(it->id);result.returned.insert(it->id);
            healthySquad-=it->healthy;
        }
        for(const auto& c:candidates) {
            if(int(squad.size())>=result.target || result.healthy-healthySquad<=6)break;
            if(!c.healthy || !c.eligible || squad.count(c.id))continue;
            squad.insert(c.id);result.added.insert(c.id);++healthySquad;
        }
        result.reserveHealthy=result.healthy-healthySquad;
        return result;
    }
    // Key 0 is a GLOBAL reset, independent of the selected mode. Probe
    // ownership is cleared by the adapter alongside this military plan.
    Result reset(const std::vector<Candidate>& candidates) {
        Result result;result.accepted=true;
        if(mainTask)result.releasedTasks.insert(mainTask);
        if(squadTask)result.releasedTasks.insert(squadTask);
        for(const auto& c:candidates) {
            result.units.insert(c.id);
            if(taskFor(c.id))result.manualUnits.insert(c.id);
        }
        mainTask=0;squadTask=0;squad.clear();pendingLosses.clear();formed=false;selected=Scope::All;
        result.status="auto_restored";
        return result;
    }
    Result split(const std::vector<Candidate>& candidates) {
        Result result;
        if(selected==Scope::Squad && formed) {
            result.accepted=true;result.status="mode_unchanged";result.task=squadTask;result.units=squad;
            return result;
        }
        if(!formed) {
            int healthy=0,eligible=0;
            for(const auto& c:candidates){healthy+=c.healthy;eligible+=c.healthy&&c.eligible;}
            int count=squadSize(healthy,eligible);
            if(!count)return result;
            for(const auto& c:candidates)if(c.healthy && c.eligible && int(squad.size())<count)squad.insert(c.id);
            formed=true;
            result.inheritedTask=mainTask;
            // A separate task identity prevents reserve-defense updates from
            // changing the squad's inherited attack/retreat/gather objective.
            squadTask=mainTask?++serial:0;
        }
        selected=Scope::Squad;
        mainTask=++serial;
        result.accepted=true;result.status="squad_split";result.task=squadTask;result.reserveTask=mainTask;result.units=squad;
        for(const auto& c:candidates)if(!squad.count(c.id))result.reserves.insert(c.id);
        return result;
    }
    Result assign(const std::vector<Candidate>& candidates) {
        Result result;
        if(selected==Scope::Squad) {
            if(!formed) {
                int healthy=0,eligible=0;
                for(const auto& c:candidates){healthy+=c.healthy;eligible+=c.healthy&&c.eligible;}
                int count=squadSize(healthy,eligible);
                if(!count)return result;
                for(const auto& c:candidates)if(c.healthy && c.eligible && int(squad.size())<count)squad.insert(c.id);
                formed=true;
            }
            result.units=squad;squadTask=++serial;result.task=squadTask;
        } else {
            for(const auto& c:candidates)result.units.insert(c.id);
            squad.clear();pendingLosses.clear();formed=false;squadTask=0;
            mainTask=++serial;result.task=mainTask;
        }
        result.accepted=true;result.status=result.units.empty()?"waiting_units":"applied";
        return result;
    }
    // 9, like 0, has global scope. It replaces both offensive and reserve
    // tasks, even when the player last selected the squad mode.
    Result defendAll(const std::vector<Candidate>& candidates) {
        select(Scope::All);return assign(candidates);
    }
};
}
