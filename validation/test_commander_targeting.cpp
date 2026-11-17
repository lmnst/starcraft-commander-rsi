#include "../app/CommanderTargeting.h"
#include <cassert>
#include <iostream>
using namespace CommanderTargeting;
Candidate building(int id,double distance) { Candidate c;c.id=id;c.building=true;c.distance=distance;c.inRange=true;return c; }
Candidate dragoon(int id,double distance,bool firing) { Candidate c;c.id=id;c.distance=distance;c.activeThreat=firing;c.nearbyThreat=true;return c; }
Candidate probe(int id,double distance,bool attacking) { Candidate c;c.id=id;c.worker=true;c.distance=distance;c.activeThreat=attacking;return c; }
int main() {
    Engagement e;
    auto ordinary=building(1,10),cannon=building(2,180);
    cannon.activeThreat=true;cannon.nearbyThreat=true;
    assert(e.choose(0,{0,0},{ordinary,cannon}).id==2);
    auto ranged=dragoon(3,200,true);
    assert(e.choose(0,{0,0},{ordinary,ranged}).id==3);
    auto worker=probe(4,70,true);
    assert(e.choose(0,{0,0},{ordinary,worker}).id==4);
    auto routeEnemy=dragoon(41,41,true);routeEnemy.armed=true;routeEnemy.inRange=false;
    worker.inRange=true;worker.attackingSelf=true;
    assert(e.choose(0,{0,0},{ordinary,worker,routeEnemy}).id==41);
    routeEnemy.activeThreat=false;routeEnemy.distance=200;
    assert(e.choose(0,{0,0},{ordinary,worker,routeEnemy}).id==41);
    worker.inRange=false;worker.attackingSelf=false;
    auto production=building(20,250);production.importantBuilding=true;
    assert(e.choose(0,{0,0},{ordinary,production}).id==20);
    assert(e.choose(0,{0,0},{production,worker}).id==4);
    assert(e.choose(0,{0,0},{production,cannon}).id==2);
    assert(e.choose(0,{0,0},{production,ranged}).id==3);
    // A generic attacking unit of any type, including melee or another race,
    // must outrank strategic structures just like a Dragoon does.
    auto otherAttacker=dragoon(21,180,true);
    assert(e.choose(0,{0,0},{production,otherAttacker}).id==21);
    worker.activeThreat=false;
    assert(e.choose(0,{0,0},{ordinary,worker}).id==4);
    assert(e.choose(0,{0,0},{worker,cannon}).id==2);
    cannon.activeThreat=false;cannon.nearbyThreat=false; // unpowered/incomplete
    assert(e.choose(0,{0,0},{cannon,ranged}).id==3);
    ranged.valid=false;
    assert(e.choose(0,{0,0},{ordinary,ranged}).id==1);
    worker.distance=450;
    assert(e.choose(0,{0,0},{ordinary,worker}).id==1);
    // Replacing a building with an attacker must happen immediately; target
    // stability can never outweigh threat tier.
    e.commit(0,{0,0},1,true,false);
    ranged.valid=true;
    assert(e.choose(1,{0,0},{ordinary,ranged}).id==3);
    e.commit(1,{0,0},3,false);
    ranged.anchorDistance=500;
    assert(e.needsRelease(2,{0,0},ranged));
    assert(e.choose(2,{0,0},{ranged}).id==-1);
    ranged.anchorDistance=350;
    assert(e.choose(2,{257,0},{ranged}).id==-1);
    assert(e.choose(1+chaseFrames,{200,0},{ranged}).id==-1);
    auto escapingProbe=probe(5,210,false);escapingProbe.valid=false;
    Engagement probeLeash;probeLeash.commit(0,{0,0},5,false);
    assert(probeLeash.needsRelease(1,{100,0},escapingProbe));
    probeLeash.release(1,{100,0});
    assert(probeLeash.choose(2,{100,0},{ordinary}).id==-1);
    escapingProbe.valid=true;escapingProbe.distance=100;
    assert(probeLeash.choose(2,{100,0},{escapingProbe,ordinary}).id==-1);
    auto freshAttacker=dragoon(10,150,true);
    assert(probeLeash.choose(2,{100,0},{escapingProbe,ordinary,freshAttacker}).id==10);
    probeLeash.commit(2,{100,0},10,false);
    assert(!probeLeash.releasing && probeLeash.anchor.x==100);
    ranged.inRange=true;
    assert(e.choose(300,{257,0},{ranged}).id==3); // can return fire without pursuit
    e.release(300,{257,0});
    ranged.inRange=false;
    assert(e.choose(301,{257,0},{ranged,ordinary}).id==-1);
    ranged.inRange=true;
    assert(e.choose(301,{257,0},{ranged}).id==3); // close active attacker may be fought
    ranged.inRange=false;ranged.distance=150;ranged.fleeing=false;
    assert(e.choose(301,{257,0},{ranged}).id==3); // don't ignore an attacker that stopped to shoot us
    ranged.fleeing=true;
    assert(e.choose(301,{257,0},{ranged}).id==-1); // but still don't follow the same fleeing enemy
    ranged.distance=200;ranged.fleeing=false;
    e.resume(300+releaseFrames,{257,0},1000);
    assert(e.releasing); // time alone cannot repeatedly renew a chase budget
    e.resume(300+releaseFrames,{400,0},1000);
    assert(!e.releasing);
    ranged.activeThreat=false;
    assert(e.choose(400,{400,0},{ranged}).id==-1); // fleeing enemy remains ignored
    assert(e.blockAutoAcquire(400,{ranged})); // normal attack-move must not reacquire it
    assert(!e.blockAutoAcquire(300+ignoreFrames,{ranged}));
    assert(e.choose(300+ignoreFrames,{400,0},{ranged}).id==3);
    // Static cannons do not kite: don't reject them just because approaching
    // their initial location requires more travel than a mobile chase budget.
    Engagement staticDefense;staticDefense.commit(0,{0,0},3,false);
    cannon.activeThreat=true;cannon.nearbyThreat=true;cannon.inRange=false;cannon.anchorDistance=300;
    assert(staticDefense.choose(1000,{300,0},{cannon}).id==2);
    staticDefense.commit(1000,{300,0},2,false,false);
    assert(!staticDefense.anchored && staticDefense.chaseSince<0);
    Engagement expired;expired.releasing=true;expired.ignored[10]=1;
    expired.commit(2,{100,0},10,false);
    assert(!expired.releasing && expired.anchored && expired.anchor.x==100);
    auto sieged=dragoon(30,350,true);sieged.stationary=true;sieged.anchorDistance=350;
    Engagement siegeApproach;siegeApproach.commit(0,{0,0},30,false);
    assert(!siegeApproach.needsRelease(1000,{300,0},sieged));
    assert(siegeApproach.choose(1000,{300,0},{ordinary,sieged}).id==30);
    std::cout<<"Threat tiers, attacking Probes, target switching, fixed chase anchor, cooldown and static-defense tests passed\n";
}
