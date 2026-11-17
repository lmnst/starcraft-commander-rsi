#include "../app/PlayerColors.h"
#include <cassert>
#include <iostream>
std::vector<std::uint8_t> image(int width,int height,int stride) {
    std::vector<std::uint8_t> b(128);
    b[0]=10;b[2]=1;b[3]=8;b[8]=std::uint8_t(width-1);b[10]=std::uint8_t(height-1);b[65]=1;b[66]=std::uint8_t(stride);
    int value=0;
    for(int y=0;y<height;++y)for(int x=0;x<stride;++x)b.push_back(x<width?std::uint8_t(value++):99);
    return b;
}
int main() {
    std::array<std::array<std::uint8_t,8>,2> ramps{};
    // Game assets can pack colors horizontally; PCX need not have two rows.
    assert(PlayerColors::decodeRamps(image(128,1,128),ramps));
    for(int n=0;n<16;++n)assert(ramps[n/8][n%8]==n);
    assert(PlayerColors::decodeRamps(image(8,2,16),ramps)); // Skip scanline padding.
    for(int n=0;n<16;++n)assert(ramps[n/8][n%8]==n);
    auto truncated=image(16,1,16);truncated.resize(133);
    assert(!PlayerColors::decodeRamps(truncated,ramps));
    auto wrong=image(16,1,16);wrong[3]=4;assert(!PlayerColors::decodeRamps(wrong,ramps));
    auto badRun=image(16,1,16);badRun.resize(128);badRun.push_back(0xc0);badRun.push_back(1);
    assert(!PlayerColors::decodeRamps(badRun,ramps));
    auto run=image(16,1,16);run.resize(128);run.insert(run.end(),{0xc8,111,0xc8,165});
    assert(PlayerColors::decodeRamps(run,ramps));assert(ramps[0][0]==111 && ramps[1][7]==165);
    std::cout<<"Horizontal/vertical PCX palettes, padding, RLE and malformed input passed\n";
}
