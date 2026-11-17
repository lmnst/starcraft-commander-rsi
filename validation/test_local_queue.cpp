#include "../app/LocalQueue.h"
#include <cassert>
#include <cstdio>
int main() {
    int packets[LocalQueue::capacity]={},count=0;
    for(int value=1;value<=10000;++value) {
        int slot=LocalQueue::reserve(count);
        if(slot>=0)packets[slot]=value;
        assert(count>=0 && count<=LocalQueue::capacity);
    }
    assert(count==LocalQueue::capacity);
    for(int i=0;i<LocalQueue::readable(count);++i)assert(packets[i]==i+1);
    int reads=0;
    for(int i=0;i<LocalQueue::readable(LocalQueue::capacity+142);++i){assert(packets[i]==i+1);++reads;}
    assert(reads==LocalQueue::capacity && LocalQueue::readable(-1)==0);
    count=0;assert(LocalQueue::reserve(count)==0 && count==1);
    std::puts("10000-packet saturation, corrupt-count bounds and reuse passed");
}
