#include "../app/CommanderRaid.h"
#include "../app/CommanderOrders.h"
#include <cassert>
#include <iostream>
using CommanderRaid::Objective;
int main() {
    Objective none;
    assert(!none.finish(true,false,false));
    Objective raid;raid.depot=100;
    assert(!raid.finish(true,true,true)); // Target still alive.
    assert(!raid.finish(false,false,false)); // Fog is not destruction.
    assert(!raid.finish(true,false,true)); // A different depot still covers the site.
    raid.lost(101);assert(!raid.destroyed); // Another expansion dies: stay on target.
    raid.lost(100);assert(raid.destroyed);
    assert(raid.finish(false,false,false)); // Native destruction event suffices, even under fog.
    assert(raid.withdrawing && !raid.finish(true,false,false)); // Never restart/finish twice.
    Objective cleared;cleared.depot=200;
    assert(cleared.finish(true,false,false)); // Complete footprint was revisited and empty.
    Objective relocated;relocated.depot=201;
    assert(!relocated.finish(true,true,false)); // A visible lifted depot is still alive.
    auto inherited=raid;
    assert(inherited.withdrawing && !inherited.finish(true,false,false)); // Split keeps withdrawal.
    // Real combat is acquired before melee range, including retreating enemies.
    assert(CommanderRaid::permits(true,false,false,true,0));
    assert(CommanderRaid::permits(false,true,false,false,732,true,false));
    assert(CommanderRaid::permits(false,false,false,false,60,true,false));
    assert(CommanderRaid::permits(false,false,false,true,755,true,false));
    assert(!CommanderRaid::permits(false,false,true,true,60,false,false));
    assert(!CommanderRaid::permits(false,false,true,false,60,false,true));
    assert(CommanderRaid::permits(false,true,true,false,60,false,true));
    assert(!CommanderRaid::permits(false,true,false,false,60,false,true));
    using CommanderRules::Point;
    Point site{992,3472},main{2112,3824},home{1056,272};
    assert(!CommanderRaid::safeWithdrawal({984,3317},site,main,home)); // Old unsafe near-site gather.
    assert(!CommanderRaid::safeWithdrawal(main,site,main,home));
    assert(CommanderRaid::safeWithdrawal({992,2800},site,main,home));
    assert(CommanderRaid::safeWithdrawal(home,site,main,home));
    // A new full-team assault replaces both the raid and returning reserve task.
    CommanderOrders::Plan p;
    std::vector<CommanderOrders::Candidate> units;
    for(int i=0;i<20;++i)units.push_back({i,true,true});
    auto full=p.assign(units);auto split=p.split(units);
    assert(split.inheritedTask==full.task && split.units.size()==5 && split.reserves.size()==15);
    p.select(CommanderOrders::Scope::All);auto next=p.assign(units);
    assert(next.task!=full.task && next.task!=split.task && next.task!=split.reserveTask);
    for(auto u:units)assert(p.taskFor(u.id)==next.task);
    std::cout<<"Raid completion, fog, fixed objective, inherited withdrawal and full-team replacement passed\n";
}
