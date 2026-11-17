#include "../app/CommanderClear.h"
#include <cassert>
#include <iostream>
using namespace CommanderClear;
int main() {
    assert(target(false,false,true,false,384)); // Fixed encounter boundary.
    assert(!target(false,false,true,false,385)); // No distant army pursuit.
    assert(!target(true,false,false,false,40)); // No worker raid.
    assert(!target(false,true,true,false,40)); // No ordinary building demolition.
    assert(target(false,true,true,true,40,true)); // Defensive structures belong to clear.
    assert(target(false,true,true,false,40,true)); // A cannon need not fire first.
    assert(!target(false,true,false,true,40)); // Ordinary building excluded even if flagged active.
    assert(approach(false,false,true,40)); // Real army movement away is not worker bait.
    assert(approach(false,true,true,40)); // May shoot within current range.
    assert(approach(false,false,false,300));
    assert(!approach(false,false,false,401));
    assert(!target(true,false,false,true,40)); // Even an attacking worker is ignored by key 7.
    assert(!target(true,false,true,true,0)); // Combat flags cannot override worker exclusion.
    assert(!target(true,true,true,true,0,true)); // Worker exclusion always takes precedence.
    Encounter encounter;
    assert(!encounter.update(10,true));
    assert(!encounter.update(11,false));assert(!encounter.update(1000,false));
    assert(!encounter.finished && !encounter.update(1001,true));
    encounter.observe(1001,{200,100});
    assert(encounter.searching(1097) && !encounter.searching(1098));
    assert(encounter.lastKnown.x==200);
    encounter.observe(1100,{210,110},7);encounter.destroyed(7);
    assert(!encounter.searching(1101)); // Confirmed destruction is not fog.
    auto inherited=encounter;assert(!inherited.finished && !inherited.update(3000,true));
    std::cout<<"Clear-enemy boundaries, target restrictions and continuous guard and bounded last-known search passed\n";
}
