#include "../app/CommanderFlight.h"
#include <cassert>
#include <iostream>
using namespace CommanderFlight;
int main() {
    assert(opportunity(true,true,0,.9,64,1));
    assert(!opportunity(true,true,15,.9,64,1));
    assert(!opportunity(true,false,0,.9,64,1));
    assert(!opportunity(true,true,0,.3,64,1));
    assert(!opportunity(false,true,0,.9,64,1));
    assert(!opportunity(true,true,0,.9,150,1));
    assert(!opportunity(true,true,0,.9,64,3));
    Window w;assert(w.canStart(0,{0,0}));
    w.begin(0,{0,0},7);
    assert(w.keepFiring(5,{0,0},true,false));
    assert(!w.keepFiring(5,{0,0},true,true));
    assert(!w.keepFiring(5,{0,0},false,false));
    assert(!w.keepFiring(5,{13,0},true,false));
    assert(!w.keepFiring(fireBudget,{0,0},true,false));
    w.end(10,{0,0});
    assert(!w.canStart(81,{1000,0}));
    assert(!w.canStart(100,{90,0}));
    assert(w.canStart(100,{96,0}));
    assert(!baseArrival(false,200));
    assert(baseArrival(false,160));
    assert(baseArrival(true,200));
    assert(!baseArrival(true,225));
    std::cout<<"Run-first fire budget, cooldown, no-pursuit and base-arrival checks passed\n";
}
