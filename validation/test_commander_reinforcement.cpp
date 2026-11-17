#include "../app/CommanderOrders.h"
#include <cassert>
#include <iostream>
#include <random>
using namespace CommanderOrders;
std::vector<Candidate> army(int n) {
    std::vector<Candidate> result;
    for(int i=1;i<=n;++i)result.push_back({i,true,true});
    return result;
}
int main() {
    Plan p;auto first=p.split(army(8));
    assert(first.units.size()==2 && first.reserves.size()==6);
    const auto initial=p.squad;int main=p.mainTask,task=p.squadTask,serial=p.serial;
    // The recorded regression: eight grows to fifteen with no further key.
    for(int n=9;n<=15;++n) {
        auto r=p.reinforce(army(n));
        assert(p.squad.size()==size_t(n/4) && r.reserveHealthy>=6);
        for(int id:initial)assert(p.squad.count(id));
        assert(p.mainTask==main && p.squadTask==task && p.serial==serial);
    }
    auto same=p.reinforce(army(15));assert(!same.changed());
    p.assign(army(15));task=p.squadTask;
    // A real casualty is absent from candidates, not merely at low health.
    auto survivors=army(15);int casualty=*p.squad.begin();
    survivors.erase(survivors.begin()+casualty-1);
    auto replacement=p.reinforce(survivors);
    assert(!p.squad.count(casualty) && replacement.lost.count(casualty) && replacement.added.size()==1 && p.squad.size()==3);
    assert(p.squadTask==task && replacement.reserveHealthy==11);
    // Every member lost: retain the action and rebuild without pressing 2.
    survivors=army(40);
    for(auto it=survivors.begin();it!=survivors.end();) {
        if(p.squad.count(it->id))it=survivors.erase(it);else ++it;
    }
    auto rebuilt=p.reinforce(survivors);
    assert(rebuilt.added.size()==9 && p.squad.size()==9 && p.squadTask==task);
    // Reserve casualties force recall, rather than robbing home of six.
    auto scarce=std::vector<Candidate>(survivors.begin(),survivors.begin()+7);
    p.reinforce(scarce);assert(p.squad.size()==1);
    scarce.pop_back();auto recalled=p.reinforce(scarce);
    assert(p.squad.empty() && recalled.reserveHealthy==6 && p.formed && p.squadTask==task);
    assert(std::string(p.assign(scarce).status)=="waiting_units");task=p.squadTask;
    auto renewed=p.reinforce(army(8));assert(p.squad.size()==2 && p.squadTask==task && renewed.reserveHealthy==6);
    // Low health and excluded Reavers do not become replacement candidates.
    Plan exclusions;exclusions.split(army(8));auto candidates=army(20);
    for(auto& c:candidates)if(c.id>2)c.eligible=false;
    assert(!exclusions.reinforce(candidates).changed() && exclusions.squad.size()==2);
    for(auto& c:candidates)if(c.id>2){c.eligible=true;c.healthy=false;}
    exclusions.reinforce(candidates);assert(exclusions.squad.empty());
    // Selecting ALL preserves automatic reinforcement until a global action.
    p.select(Scope::All);p.reinforce(army(40));assert(p.squad.size()==10);
    p.defendAll(army(40));p.reinforce(army(60));assert(!p.formed && p.squad.empty());
    p.split(army(40));p.reset(army(40));p.reinforce(army(60));assert(!p.formed && p.squad.empty());
    p.split(army(40));p.select(Scope::All);p.assign(army(40));p.reinforce(army(60));assert(!p.formed && p.squad.empty());
    // Random production, health, eligibility and deaths: guard the reserve
    // floor and task continuity across repeated automatic updates.
    std::mt19937 rng(20261117);Plan random;random.split(army(40));
    main=random.mainTask;task=random.squadTask;serial=random.serial;
    for(int step=0;step<10000;++step) {
        std::vector<Candidate> available;
        for(int id=1;id<=80;++id)if(rng()%5)available.push_back({id,rng()%4!=0,rng()%6!=0});
        auto r=random.reinforce(available);
        assert(int(random.squad.size())<=r.target);
        assert(r.reserveHealthy>=std::min(6,r.healthy));
        for(int id:r.added) {
            auto c=std::find_if(available.begin(),available.end(),[&](const Candidate& c){return c.id==id;});
            assert(c!=available.end() && c->healthy && c->eligible);
        }
        assert(random.mainTask==main && random.squadTask==task && random.serial==serial);
        assert(!random.reinforce(available).changed());
    }
    std::cout<<"Dynamic squad: growth, casualties, wipe/rebuild, six healthy reserves, exclusions, reset and 10000 churn steps passed\n";
}
