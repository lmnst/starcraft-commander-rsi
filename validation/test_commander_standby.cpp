#include "../app/CommanderStandby.h"
#include <cassert>
#include <iostream>
using namespace CommanderStandby;
int main() {
    World world;
    world.buildings.push_back({{0,0},100,80});
    world.lanes.push_back({{0,0},{-400,0},24}); // Mineral route.
    world.lanes.push_back({{200,0},{330,0},16}); // Production exit.
    assert(!open({110,0},24,world)); // Unit footprint, not just point.
    assert(!open({-200,20},24,world));
    assert(!open({270,15},24,world));
    assert(open({300,150},24,world));
    assert(segmentDistance({50,30},{0,0},{100,0})==30);
    assert(segmentDistance({10,0},{0,0},{0,0})==10);
    auto front=preferred({40,0},true,{0,0},{1000,0});
    assert(front.x==384 && front.y==0);
    auto field=preferred({2000,1000},false,{0,0},{1000,0});
    assert(field.x==2000 && field.y==1000); // No implicit home retreat.
    assert(!area({0,0},true,{0,0},{0,0}));
    assert(area(front,true,{0,0},{0,0}));
    assert(!area({0,0},false,{0,0},{2000,1000}));
    for(int count:{2,6,20,50,100}) {
        auto slots=offsets(count);assert(slots.size()>=size_t(count));
        for(int i=0;i<count;++i)for(int j=i+1;j<count;++j)assert(distance(slots[i],slots[j])>=spacing);
    }
    Guard g;Point post{0,0};
    assert(!g.permits(0,{300,0},post,{350,0},false)); // Form up first.
    assert(g.permits(1,{30,0},post,{150,0},false));
    g.attack(1,10,false);
    assert(g.permits(2,{100,0},post,{180,0},false));
    g.attack(2,11,false); // Target switches do not reset the timer/post.
    assert(g.since==1);
    assert(!g.permits(3,{129,0},post,{180,0},false));
    assert(!g.permits(3,{100,0},post,{193,0},false));
    assert(!g.permits(1+chaseFrames,{100,0},post,{180,0},false));
    assert(g.permits(1+chaseFrames,{100,0},post,{300,0},true)); // Stationary ranged fire.
    g.noTarget(100,{100,0},post);
    assert(g.returning && !g.permits(101,{0,0},post,{10,0},true));
    g.ready(200,{100,0},post);assert(g.returning); // Time alone cannot renew interception.
    g.ready(201,{0,0},post);assert(!g.returning && g.since<0);
    assert(g.permits(202,{0,0},post,{100,0},false));
    std::cout<<"Standby: building footprints, mining/production lanes, perimeter/field location, formation spacing and fixed-post interception passed\n";
}
