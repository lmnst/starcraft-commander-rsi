#include "../app/ProbeCommandGuard.h"
#include <cassert>
#include <iostream>
static int calls=0,blocked=0;
static bool __fastcall original(BWAPI::Unit,void*,BWAPI::UnitCommand) {++calls;return true;}
static bool allow(void*,BWAPI::Unit,const BWAPI::UnitCommand& cmd) {
    if(cmd.getType()==BWAPI::UnitCommandTypes::Attack_Move){++blocked;return false;}return true;
}
__declspec(noinline) bool issue(BWAPI::Unit u,BWAPI::UnitCommand c){return u->issueCommand(c);}
int main() {
    std::vector<void*> table(513,reinterpret_cast<void*>(&original));table[0]=nullptr;
    struct Fake{void** vptr;};Fake a{table.data()+1},b{table.data()+1};
    auto u=reinterpret_cast<BWAPI::Unit>(&a),other=reinterpret_cast<BWAPI::Unit>(&b);
    int owner=0;
    assert(ProbeCommandGuard::attach(u,&owner,allow));
    assert(ProbeCommandGuard::count(&owner)==1 && a.vptr!=table.data()+1);
    assert(!issue(u,BWAPI::UnitCommand::attack(u,BWAPI::Position(20,20))));
    assert(blocked==1 && calls==0);
    assert(issue(u,BWAPI::UnitCommand::returnCargo(u)) && calls==1);
    assert(issue(other,BWAPI::UnitCommand::attack(other,BWAPI::Position(20,20))) && calls==2);
    ProbeCommandGuard::releaseAll(&owner);assert(a.vptr==table.data()+1);
    assert(issue(u,BWAPI::UnitCommand::attack(u,BWAPI::Position(20,20))) && calls==3);
    std::cout<<"Per-object command gate: blocked combat, preserved economy, other units untouched, original table restored\n";
}
