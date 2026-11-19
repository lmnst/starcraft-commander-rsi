#include "../app/CommanderKeys.h"
#include "../app/CommanderHudText.h"
#include <cassert>
int main() {
    using namespace CommanderKeys;
    for(int vk='1';vk<='8';++vk)assert(shortcut(vk,false,false,false)==vk);
    assert(shortcut('9',false,false,false)==safeRally);
    assert(shortcut('0',false,false,false)==defend);
    assert(shortcut(topRowMinus,false,false,false)==reset);
    assert(physical(safeRally)=='9' && physical(defend)=='0' && physical(reset)=='-');
    for(int vk : std::array<int,4>{{'8','9','0',topRowMinus}}) {
        assert(!shortcut(vk,true,false,false));
        assert(!shortcut(vk,false,true,false));
        assert(!shortcut(vk,false,false,true));
    }
    assert(heldIndex(topRowMinus)==10);
    for(int vk : {0x60,0x69,0x6D,0x70,0x79}) {
        assert(heldIndex(vk)==-1);
        assert(!shortcut(vk,false,false,false));
    }
    CommanderHud::Data d;
    auto lines=CommanderHud::lines(d);
    assert(lines[0].find(L"- 全局重置")!=std::wstring::npos);
    assert(lines[1].find(L"9 撤离集结  0 回防")!=std::wstring::npos);
    d.status="applied";d.lastKey=8;d.lastSafeRally=true;
    assert(CommanderHud::feedback(d).find(L"安全撤离集结")!=std::wstring::npos);
    d.lastKey=9;
    assert(CommanderHud::feedback(d).find(L"再按0")!=std::wstring::npos);
    d.status="mining_locked";
    assert(CommanderHud::feedback(d).find(L"-解除")!=std::wstring::npos);
}
