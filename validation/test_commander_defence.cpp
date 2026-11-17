#include "../app/CommanderDefence.h"
#include <cassert>
#include <iostream>
using namespace CommanderDefence;
int main() {
    assert(arrived(false,500,500,500)); // Reach the perimeter, not Nexus center.
    assert(arrived(false,680,100,500)); // Protect an outlying building immediately.
    assert(!arrived(false,900,500,500)); // En route remains run-first.
    assert(candidate(true,true,true,true,700,350,192)); // Ranged attacker shooting a worker/building.
    assert(!candidate(true,true,true,true,769,20,192)); // Bait outside the locked zone.
    assert(!candidate(false,true,true,true,400,20,192)); // No pursuit into fog.
    assert(!candidate(true,true,false,false,300,20,0)); // Harmless scout/economic building.
    Guard side;assert(side.allow(0,{300,0},{0,0},{480,0},1,false));
    assert(side.allow(50,{400,0},{0,0},{500,0},2,false)); // Changing target does not move anchor.
    assert(side.origin.x==300 && side.since==0);
    assert(!side.allow(144,{400,0},{0,0},{500,0},3,false) && side.returning); // Time budget.
    assert(!side.ready(240,{600,0},{300,0})); // Cooldown alone cannot re-arm a chase.
    assert(side.ready(240,{300,0},{300,0})); // Must actually return to the defense post.
    Guard bait;assert(bait.allow(0,{400,0},{0,0},{600,0},10,false));
    assert(!bait.allow(10,{593,0},{0,0},{650,0},11,false)); // Fixed spatial origin, even after target switch.
    Guard border;assert(border.allow(0,{600,0},{0,0},{750,0},1,false));
    assert(!border.allow(1,{620,0},{0,0},{769,0},1,false)); // Crossing zone causes recall.
    Guard vanished;assert(vanished.allow(0,{400,0},{0,0},{580,0},1,false));
    vanished.noTarget(20,{500,0},{300,0});assert(vanished.returning); // Lost target -> regroup, not search.
    Guard hold;assert(hold.allow(0,{400,0},{0,0},{450,0},1,true));
    assert(hold.allow(1000,{400,0},{0,0},{450,0},1,true)); // Can hold and defend without chasing.
    std::cout<<"Perimeter entry, threatened assets, side intercept, fixed leash, fog and recall passed\n";
}
