#pragma once
namespace LocalQueue {
// Startup/map transfer can burst past 16 packets before the peer is scheduled.
// The shared array uses this same bound; never increment beyond its storage.
constexpr int capacity=256;
inline int readable(int count) { return count<0?0:count>capacity?capacity:count; }
inline int reserve(int& count) {
    if(count<0)count=0;
    if(count>=capacity){count=capacity;return -1;}
    return count++;
}
}
