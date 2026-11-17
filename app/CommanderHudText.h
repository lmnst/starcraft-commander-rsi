#pragma once
#include "CommanderOrders.h"
#include <array>
#include <string>

namespace CommanderHud {
inline const wchar_t* scope(CommanderOrders::Scope value) {
    return value==CommanderOrders::Scope::Squad?L"小队":L"全队";
}
inline const wchar_t* action(int key) {
    switch(key) {
        case 1:return L"探机避险";case 2:return L"强制采矿";case 3:return L"小队模式";case 4:return L"全队模式";
        case 5:return L"近敌/分矿建筑";case 6:return L"进攻主基地";case 7:return L"区域警戒";
        case 8:return L"安全集结";case 9:return L"全军回防";case 0:return L"全局重置";default:return L"自动作战";
    }
}
inline std::wstring task(CommanderOrders::Action value,const std::string& phase) {
    if(phase=="waiting_units")return L"等待作战单位";
    if(phase=="waiting_reinforcements")return L"等待自动补兵";
    if(phase=="lost")return L"小队已损失";
    using A=CommanderOrders::Action;
    if(value==A::MainBase)return phase=="engaging"?L"攻主基地·迎敌":L"攻主基地·推进";
    if(value==A::Expansion)return phase=="engaging"?L"攻近敌/分矿·迎敌":L"攻近敌/分矿·推进";
    if(value==A::Clear)return phase=="patrol_engaging"?L"区域警戒·迎敌":phase=="patrol_searching"?L"失去视野·局部搜索":L"区域警戒·待命";
    if(value==A::Gather)return phase=="holding"?L"集结待命":L"正在集结";
    if(value==A::Retreat) {
        if(phase=="rally_withdrawing")return L"脱战后汇合";
        if(phase=="rally_joining")return L"前往汇合点";
        if(phase=="rally_waiting")return L"等援兵待命";
        return phase=="safe"?L"已安全撤离":phase=="regrouping"?L"安全汇合":L"正在撤退";
    }
    return phase=="returning"?L"回防途中":phase=="intercepting"?L"外围拦截":phase=="reforming"?L"收回防线":phase=="defending"?L"基地交战":L"外围防守";
}
inline const wchar_t* scout(const std::string& value) {
    if(value=="evading")return L"正在避险";
    if(value=="diverting")return L"换路侦察";
    if(value=="returning")return L"正在返家";
    if(value=="home_safe")return L"已安全返家";
    if(value=="lost")return L"探机已损失";
    return L"未接管";
}
struct Data {
    CommanderOrders::Scope mode=CommanderOrders::Scope::All,lastMode=CommanderOrders::Scope::All;
    int selected=0,army=0,lastKey=0,commandUnits=0,reserves=0;
    bool manualCamera=true,miningLocked=false;
    std::string status="ready",scoutStatus="idle";
    std::wstring mainTask=L"自动作战",squadTask=L"未编组";
    std::wstring notice;
};
inline std::wstring feedback(const Data& d) {
    const auto& s=d.status;
    if(s=="no_scout_probe")return L"没有可接管的避险探机，保持当前任务。";
    if(s=="scout_evade_applied")return L"已接管探机避险；其余部队和探机保持原任务。";
    if(s=="mining_locked")return L"强制采矿已开启：屏蔽追击和侦察，保留采气、建造；0解除。";
    if(s=="mining_unavailable")return L"采矿接管未成功，已保留原控制；请查看日志。";
    if(s=="mode_selected")return L"已选全队范围，原任务继续；5～8下令，9始终全军回防。";
    if(s=="squad_split")return L"小队 "+std::to_wstring(d.commandUnits)+L" 名继续原任务，其余 "+std::to_wstring(d.reserves)+L" 名回防；后续自动补兵。";
    if(s=="mode_unchanged")return L"小队原任务继续；按约四分之一自动补兵，至少留6名健康兵回防。";
    if(s=="expansion_unknown")return L"未发现基地附近敌方建筑或敌方分矿，保持原任务。";
    if(s=="not_enough_healthy_units")return L"未分队：需2名可用成员，并保留6名健康主力。";
    if(s=="waiting_units")return L"指令已记录，等待可用作战单位。";
    if(s=="auto_restored")return L"已全部重置：探机采矿锁及人工任务解除，交回Bot。";
    if(s=="task_confirmed")return L"原任务继续：保留集火、追击边界和警戒区域。";
    if(s=="applied" && (d.lastKey==5 || d.lastKey==6))return std::wstring(L"已下令：")+scope(d.lastMode)+L" · "+action(d.lastKey)+L"；沿途先打敌军，再攻建筑，完成后撤离待命。";
    if(s=="applied" && d.lastKey==7)return std::wstring(L"已下令：")+scope(d.lastMode)+L"持续区域警戒；忽略探机，敌军再现自动接战。";
    if(s=="applied" && d.lastKey==8)return std::wstring(L"已下令：")+scope(d.lastMode)+L"先脱险，再安全汇合；不因探机改变汇合点。";
    if(s=="applied" && d.lastKey==9)return L"全军回防：分守主矿和分矿，忽略探机诱导；再按9重分配。";
    if(s=="applied")return std::wstring(L"已下令：")+scope(d.lastMode)+L" · "+action(d.lastKey)+L"（"+std::to_wstring(d.commandUnits)+L"名）。";
    return L"1避险；2强制采矿；3/4选范围；5～8下令；9全军回防；0重置。";
}
inline std::array<std::wstring,4> lines(const Data& d) {
    return {{
        L"1 探机避险   2 强制采矿   3 小队模式   4 全队模式   0 全局重置",
        L"5 近敌/分矿建筑   6 进攻主基地   7 区域警戒   8 安全集结   9 全军回防",
        std::wstring(scope(d.mode))+(d.mode==CommanderOrders::Scope::Squad?L"（"+std::to_wstring(d.selected)+L"）":L"")+L"｜作战部队："+std::to_wstring(d.army)+L"｜主力："+d.mainTask+L"｜小队："+d.squadTask+L"｜矿锁："+(d.miningLocked?L"开":L"关"),
        L"提示："+(d.notice.empty()?feedback(d):d.notice)
    }};
}
}
