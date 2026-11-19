#include "../app/CommanderMainArea.h"
#include <cassert>
int main() {
    using namespace CommanderMainArea;
    assert(contains({400,0},{0,0},{{900,0}}));
    assert(!contains({700,0},{0,0},{{900,0}})); // Neighbouring resource base is excluded.
    assert(!contains({769,0},{0,0},{}));
    assert(powers({0,0},{0,0}) && !powers({0,0},{255,159}));
    assert(!powers({0,0},{256,0}));
    std::vector<Building> b{{1,true,false,false,0,40,1},{2,false,true,false,0,140,1},{3,false,false,false,0,130,.2}};
    assert(choose(b,-1)==2); // Pylon before production/core, despite the core being closest.
    assert(choose(b,3)==3); // Finish the current outer building.
    assert(choose(b,1)==2); // An overlooked outer building interrupts the core.
    b.push_back({4,false,false,true,0,100,1});assert(choose(b,-1)==4);
    b={{1,true,false,false,0,40,1},{2,false,true,false,0,600,1},{3,false,false,false,0,100,1}};
    assert(choose(b,-1)==3); // Never cross the base for a distant pylon first.
    b={{2,false,true,false,0,150,1},{3,false,true,false,2,180,1}};assert(choose(b,-1)==3);
    assert(!complete(1,0,false,false,1000)); // Destroying the Nexus is not clearing the area.
    assert(!complete(0,1,false,false,1000)); // Fog is not success.
    assert(!complete(0,0,true,false,1000));
    assert(!complete(0,0,false,true,1000));
    assert(!complete(0,0,false,false,95));
    assert(complete(0,0,false,false,96));
}
