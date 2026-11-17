#include "../app/CommanderMining.h"
#include <cassert>
#include <iostream>
int main() {
    using namespace CommanderMining;
    assert(builderApproach(1088,416,3000));
    assert(!builderApproach(1088,432,3000)); // Frozen scout route has a half-tile Y offset.
    assert(!builderApproach(1088,416,640)); // Enemy-main-bound routes cannot be builders.
    assert(!builderApproach(-32,416,3000));
    assert(preserveBuild(120,168,false,false)); // Pending path must finish before miner recall.
    assert(!preserveBuild(169,168,false,false)); // Failed placement cannot exempt a worker forever.
    assert(!preserveBuild(120,168,true,false)); // Protoss worker is free once warp-in starts.
    assert(preserveBuild(200,168,true,true)); // A real construction task takes precedence.
    std::cout<<"Mining lock preserves real construction, expires stale builder paths and excludes scout moves\n";
}
