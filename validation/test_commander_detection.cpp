#include "../app/CommanderDetection.h"
#include <cassert>
int main() {
    using namespace CommanderDetection;
    assert(safeCombat({0,0},{100,0},{0,300}));
    assert(!safeCombat({0,0},{0,500},{0,300})); // Endpoints alone would miss crossing the hidden attacker's area.
    assert(!safeCombat({0,0},{100,0},{0,200}));
    assert(observable(true,true,false,0)); // Hidden HP must not look like death.
    assert(!observable(true,true,true,0));
    assert(!observable(true,false,false,0));
    assert(!observable(false,true,false,0));
    for(int k: {5,6,7})assert(protects(k,false,false));
    for(int k: {0,1,2,3,4,8,9})assert(!protects(k,false,false));
    assert(!protects(6,true,false) && !protects(6,false,true));
    assert(evade(100,false) && evade(280,true) && !evade(280,false));
    assert(!release(true,false,false,1000,300));
    assert(!release(true,false,false,80,500));
    assert(release(true,true,false,0,100));
    assert(release(false,false,false,0,100));
    assert(release(true,false,true,0,300));
    assert(release(true,false,false,241,449));
}
