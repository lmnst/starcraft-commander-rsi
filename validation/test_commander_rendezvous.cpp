#include "../app/CommanderRendezvous.h"
#include "../app/CommanderOrders.h"
#include <cassert>
#include <iostream>
using namespace CommanderRendezvous;
int main() {
    Point home{0,0},front{1000,0},rally{600,0};
    assert(frontUnit(home,front,rally,{900,0},false));
    assert(!frontUnit(home,front,rally,{200,0},false));
    auto rear=choose({200,0},rally,rally,home,false,true,{},{{home,true},{rally,true}});
    assert(rear.moving && rear.position.x==600); // No home-bound retreat for safe rear units.
    std::vector<Hazard> road{{{500,0},120,1}};
    assert(!safeLeg({200,0},rally,road));
    auto detour=choose({200,0},rally,rally,home,false,true,road,{{rally,true},{{400,250},true},{home,true}});
    assert(detour.moving && detour.position.y==250 && detour.routePeak==0);
    auto blocked=choose({200,0},rally,rally,home,false,true,road,{{rally,true},{home,true}});
    assert(!blocked.moving && blocked.position.x==200); // Wait, do not run into enemy or retreat to home.
    auto bend=choose({200,0},{250,150},rally,home,false,true,{},{{{150,150},true,true}});
    assert(bend.moving); // A verified shorter ground route may bend around terrain.
    std::vector<Hazard> contact{{{1200,0},288,2}};
    auto escape=choose(front,{800,0},rally,{0,0},false,false,contact,{{{1400,0},true},{{800,0},true}});
    assert(escape.moving && escape.position.x==800 && escape.risk==0);
    assert(escape.routePeak<=exposure(front,contact));
    // A formerly safe cached path becomes invalid when an enemy enters it.
    assert(safeLeg({0,0},{300,0},{}));
    assert(!safeLeg({0,0},{300,0},{{{150,0},96,1}}));
    CommanderOrders::Plan plan;
    std::vector<CommanderOrders::Candidate> army;
    for(int i=1;i<=20;++i)army.push_back({i,true,true});
    auto all=plan.assign(army);assert(plan.taskFor(21)==all.task); // New production joins full task.
    auto split=plan.split(army);auto small=plan.assign(army);
    assert(plan.taskFor(21)==split.reserveTask && plan.taskFor(*small.units.begin())==small.task); // Never recruit into fixed squad.
    plan.reset(army);assert(plan.taskFor(21)==0);
    std::cout<<"Shared rally, frontline/rear roles, no rear reversal, safe detour, blocked-route wait and scope passed\n";
}
