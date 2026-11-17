#include "../app/CommanderOrders.h"
#include <cassert>
#include <iostream>
using namespace CommanderOrders;
std::vector<Candidate> army(int n) {
    std::vector<Candidate> result;for(int i=1;i<=n;++i)result.push_back({i,true,true});return result;
}
int main() {
    assert(squadSize(7,7)==0 && squadSize(8,8)==2);
    assert(squadSize(20,20)==5 && squadSize(40,40)==10);
    assert(squadSize(20,3)==3 && squadSize(20,1)==0);
    Plan p;
    auto whole=p.assign(army(20));assert(whole.accepted && whole.units.size()==20);
    int firstMain=p.mainTask;
    p.select(Scope::Squad);
    assert(p.mainTask==firstMain && !p.formed); // mode alone changes nothing
    auto first=p.assign(army(20));assert(first.accepted && first.units.size()==5);
    auto ids=first.units;
    assert(p.mainTask==firstMain && p.taskFor(20)==firstMain);
    auto more=army(40);
    for(int action=0;action<5;++action) {
        auto next=p.assign(more);
        assert(next.units==ids && next.task>first.task); // every action same squad
        for(int id:ids)assert(p.taskFor(id)==next.task);
        assert(p.taskFor(40)==firstMain); // new production stays with main force
    }
    int lastSquad=p.squadTask;
    p.select(Scope::All);
    assert(p.squad==ids && p.squadTask==lastSquad); // no premature recall
    auto all=p.assign(more);assert(all.units.size()==40 && p.squad.empty() && !p.formed);
    for(int id:ids)assert(p.taskFor(id)==all.task); // all overrides old squad
    p.select(Scope::Squad);
    auto fresh=p.assign(more);assert(fresh.units.size()==10);
    auto alive=fresh.units;alive.erase(*alive.begin());p.prune(alive);
    auto reduced=p.assign(more);assert(reduced.units==alive && reduced.units.size()==9);
    p.prune({});
    auto lost=p.assign(more);assert(lost.accepted && p.formed && p.squad.empty() && std::string(lost.status)=="waiting_units");
    p.select(Scope::All);p.assign(more);p.select(Scope::Squad);
    assert(p.assign(more).units.size()==10); // reset only via a whole-team action
    Plan insufficient;insufficient.select(Scope::Squad);
    auto weak=army(20);for(auto& c:weak)c.healthy=c.id<=7;
    assert(!insufficient.assign(weak).accepted && !insufficient.formed);
    assert(insufficient.mainTask==0 && insufficient.squadTask==0);
    Plan split;
    auto attack=split.assign(army(20));
    auto divided=split.split(army(20));
    assert(divided.accepted && divided.units.size()==5 && divided.reserves.size()==15);
    assert(divided.inheritedTask==attack.task && divided.reserveTask==split.mainTask);
    assert(split.squadTask==divided.task && divided.task!=attack.task);
    for(int id:divided.units)assert(!divided.reserves.count(id) && split.taskFor(id)==divided.task);
    for(int id:divided.reserves)assert(split.taskFor(id)==divided.reserveTask);
    auto repeated=split.split(army(40));
    assert(repeated.units==divided.units && repeated.reserveTask==0);
    assert(split.mainTask==divided.reserveTask && split.squadTask==divided.task);
    split.select(Scope::All);
    auto again=split.split(army(40));
    assert(again.units==divided.units && again.reserves.size()==35 && again.task==divided.task);
    assert(split.taskFor(40)==again.reserveTask); // new production goes home
    Plan cannotSplit;auto ongoing=cannotSplit.assign(army(7));
    auto rejected=cannotSplit.split(army(7));
    assert(!rejected.accepted && cannotSplit.selected==Scope::All && cannotSplit.mainTask==ongoing.task && !cannotSplit.formed);
    // Exact regression: full attack -> split (reserve defense) -> select ALL
    // -> attack again. Every old reserve and squad member, plus reinforcements,
    // must have the one new attack task; merely selecting ALL preserves tasks.
    Plan rejoin;rejoin.assign(army(20));
    auto separated=rejoin.split(army(20));
    int oldReturn=separated.reserveTask,oldSquad=separated.task;
    rejoin.select(Scope::All);
    assert(rejoin.mainTask==oldReturn && rejoin.squadTask==oldSquad);
    auto reunited=rejoin.assign(army(23));
    assert(reunited.accepted && reunited.units.size()==23);
    assert(rejoin.squad.empty() && !rejoin.formed && rejoin.squadTask==0);
    for(int id=1;id<=23;++id) {
        assert(rejoin.taskFor(id)==reunited.task);
        assert(rejoin.taskFor(id)!=oldReturn && rejoin.taskFor(id)!=oldSquad);
    }
    // Global reset must release BOTH groups even while SQUAD is selected,
    // including empty/waiting tasks, and must not rewind task serial numbers.
    for(bool squadMode:{false,true}) {
        Plan reset;reset.assign(army(20));auto separate=reset.split(army(20));
        if(!squadMode)reset.select(Scope::All);
        int serial=reset.serial;
        auto released=reset.reset(army(23));
        assert(released.accepted && released.releasedTasks.count(separate.task) && released.releasedTasks.count(separate.reserveTask));
        assert(released.manualUnits.size()==23 && reset.mainTask==0 && reset.squadTask==0 && !reset.formed && reset.squad.empty());
        assert(reset.selected==Scope::All);
        for(int id=1;id<=24;++id)assert(reset.taskFor(id)==0); // future production stays automatic
        auto repeat=reset.reset(army(23));assert(repeat.manualUnits.empty() && repeat.releasedTasks.empty());
        auto next=reset.assign(army(24));assert(next.task>serial && next.units.size()==24);
        for(int id=1;id<=24;++id)assert(reset.taskFor(id)==next.task);
        reset.split(army(24));reset.assign(army(24)); // squad action can reacquire after reset
        assert(reset.selected==Scope::Squad && reset.squadTask>next.task);
    }
    Plan empty;empty.assign({});assert(empty.mainTask>0);empty.reset({});assert(empty.mainTask==0);
    Plan home;home.assign(army(20));auto homeSplit=home.split(army(20));
    auto global=home.defendAll(army(23));
    assert(global.accepted && global.units.size()==23 && home.selected==Scope::All && !home.formed && home.squad.empty() && home.squadTask==0);
    for(int id=1;id<=24;++id)assert(home.taskFor(id)==global.task && home.taskFor(id)!=homeSplit.task && home.taskFor(id)!=homeSplit.reserveTask);
    home.split(army(23));home.prune({});
    assert(home.defendAll(army(23)).units.size()==23); // 8 also recovers from a lost squad.
    std::cout<<"Squad membership, global reset, future units and manual reacquisition passed\n";
}
