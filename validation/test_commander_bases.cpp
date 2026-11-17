#include "../app/CommanderBases.h"
#include <cassert>
#include <iostream>
using namespace CommanderBases;
std::vector<Soldier> army(int count) {
    std::vector<Soldier> result;
    for(int i=1;i<=count;++i)result.push_back({i,{double(i*8),0},2,1,i>17,{1,2,3}});
    return result;
}
std::map<int,int> counts(const Defence& d) {std::map<int,int> r;for(auto a:d.assigned)++r[a.second];return r;}
int main() {
    std::vector<Base> bases{{1,{0,0},true},{2,{1000,0},false}};
    auto soldiers=army(20);Defence d;d.update(bases,soldiers);
    assert(d.wanted.at(1)==14 && d.wanted.at(2)==6);
    assert(counts(d).at(1)==14 && counts(d).at(2)==6);
    auto old=d.assigned;d.update(bases,soldiers);assert(d.assigned==old);
    bases[1].pressure=12;d.update(bases,soldiers);
    int reinforced=counts(d).at(2);assert(reinforced>6 && counts(d).at(1)>=4);
    int moved=0;for(auto a:d.assigned)moved+=old.at(a.first)!=a.second;
    assert(moved==reinforced-6); // Only the additional reinforcements move.
    bases[1].pressure=0;d.update(bases,soldiers);assert(counts(d).at(2)==reinforced && d.reinforced);
    d=Defence{};d.update(bases,soldiers);assert(counts(d).at(2)==6 && !d.reinforced); // Pressing 8 anew.
    bases[0].pressure=40;bases[1].pressure=100;d.update(bases,soldiers);
    assert(counts(d).at(1)>=4 && counts(d).at(2)>6);
    bases.resize(1);d.update(bases,soldiers);assert(counts(d).at(1)==20 && !counts(d).count(2));
    bases.push_back({2,{1000,0},false});bases[0].pressure=0;d=Defence{};
    d.update(bases,soldiers); // Incomplete depots are normal eligible sites supplied by the adapter.
    bases.push_back({3,{0,1000},false});d=Defence{};d.update(bases,soldiers);
    assert(counts(d).at(1)==14 && counts(d).at(2)==3 && counts(d).at(3)==3);
    for(int sites=1;sites<=8;++sites)for(int size=0;size<=40;++size) {
        std::vector<Base> many;for(int i=1;i<=sites;++i)many.push_back({i,{double(i*100),0},i==1,i==sites?10.0:0.0});
        auto q=quotas(size,many,2);int sum=0;for(auto v:q){assert(v.second>=0);sum+=v.second;}assert(sum==size);
    }
    Defence isolated;auto blocked=army(4);
    blocked[0].reachable.clear();for(size_t i=1;i<blocked.size();++i)blocked[i].reachable={1};
    isolated.update(bases,blocked);assert(!isolated.assigned.count(1) && counts(isolated).at(1)==3);
    auto survivors=army(10);d.update(bases,survivors);
    assert(d.assigned.size()==10);for(auto a:d.assigned)assert(a.first<=10);
    bases[1].pressure=10;bases[1].airThreat=true;
    Defence air;air.update(bases,soldiers);
    for(int id=18;id<=20;++id)assert(air.assigned.at(id)==2);
    Defence deployed;
    for(int id=1;id<=20;++id)deployed.assigned[id]=id<=6?2:1;
    deployed.update(bases,soldiers);
    for(int id=18;id<=20;++id)assert(deployed.assigned.at(id)==2); // Swap already stationed anti-air defenders too.
    auto guardPool=army(15);Defence reserved;reserved.update(bases,guardPool);
    for(auto a:reserved.assigned)assert(a.first<=15); // An attack squad is absent from the eligible pool.
    std::cout<<"Multi-base: 70/30, pressure reinforcement/retention/reset, losses/new units, unreachable sites, air preference and scoped reserve pools passed\n";
}
