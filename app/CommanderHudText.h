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
        case 5:return L"近敌/分矿建筑";case 6:return L"主基地区域清场";case 7:return L"区域警戒";
        case 8:return L"原地集结";case 9:return L"全军回防";case 0:return L"全局重置";default:return L"自动作战";
    }
}
inline std::wstring task(CommanderOrders::Action value,const std::string& phase) {
    if(phase=="waiting_units")return L"等待作战单位";
    if(phase=="waiting_reinforcements")return L"等待自动补兵";
    if(phase=="lost")return L"小队已损失";
    if(phase=="detection_skirmishing")return L"就近迎敌·兼顾反隐";
    if(phase=="assembly_shared_refuge")return L"集结·共同避险";
    if(phase=="main_outer_pylon")return L"清场·拆外围水晶";
    if(phase=="main_outer_building")return L"清场·拆外围建筑";
    if(phase=="main_core")return L"清场·拆主建筑";
    if(phase=="main_combat")return L"清场·先迎敌军炮台";
    if(phase=="main_surveying")return L"清场·扫查迷雾";
    if(phase=="main_confirming")return L"清场·确认区域";
    if(phase=="main_blocked")return L"清场·目标暂不可达";
    if(phase=="main_advancing")return L"清场·从外围推进";
    using A=CommanderOrders::Action;
    if(phase=="evading_cloak")return std::wstring(value==A::Gather?L"原地集结":value==A::Clear?L"警戒":value==A::Expansion?L"攻近敌/分矿":L"攻主基地")+L"·避隐刀";
    if(phase=="waiting_detection")return std::wstring(value==A::Gather?L"原地集结":value==A::Clear?L"警戒":value==A::Expansion?L"攻近敌/分矿":L"攻主基地")+L"·等反隐";
    if(phase=="assembly_waiting")return L"原地等援";
    if(phase=="assembly_joining")return L"援兵汇合";
    if(phase=="assembly_defending")return L"集结区还击";
    if(phase=="assembly_evading")return L"集结·局部避险";
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
    bool manualCamera=true,miningLocked=false,lastSafeRally=false;
    std::string status="ready",scoutStatus="idle";
    std::wstring mainTask=L"自动作战",squadTask=L"未编组";
    std::wstring notice;
};
inline std::wstring feedback(const Data& d) {
    const auto& s=d.status;
    if(s=="no_scout_probe")return L"没有可接管的避险探机，保持当前任务。";
    if(s=="scout_evade_applied")return L"已接管探机避险；其余部队和探机保持原任务。";
    if(s=="mining_locked")return L"强制采矿已开启：屏蔽追击和侦察，保留采气、建造；-解除。";
    if(s=="mining_unavailable")return L"采矿接管未成功，已保留原控制；请查看日志。";
    if(s=="mode_selected")return L"已选全队范围，原任务继续；5～9下令，0始终全军回防。";
    if(s=="squad_split")return L"小队 "+std::to_wstring(d.commandUnits)+L" 名继续原任务，其余 "+std::to_wstring(d.reserves)+L" 名回防；后续自动补兵。";
    if(s=="mode_unchanged")return L"小队原任务继续；按约四分之一自动补兵，至少留6名健康兵回防。";
    if(s=="expansion_unknown")return L"未发现基地附近敌方建筑或敌方分矿，保持原任务。";
    if(s=="not_enough_healthy_units")return L"未分队：需2名可用成员，并保留6名健康主力。";
    if(s=="waiting_units")return L"指令已记录，等待可用作战单位。";
    if(s=="auto_restored")return L"已全部重置：探机采矿锁及人工任务解除，交回Bot。";
    if(s=="patrol_reanchored")return L"警戒位置已更新：各部就近迎敌，保留集火，忽略探机。";
    if(s=="task_confirmed" && d.lastKey==6)return L"主基地区域清场继续：保留目标、集火和扫查进度。";
    if(s=="task_confirmed")return L"原任务继续：保留集火和追击边界；7可更新警戒位置。";
    if(s=="applied" && d.lastKey==5)return std::wstring(L"已下令：")+scope(d.lastMode)+L" · "+action(d.lastKey)+L"；沿途先迎敌，反隐等待仍可安全交战，到位后续攻。";
    if(s=="applied" && d.lastKey==6)return std::wstring(L"已下令：")+scope(d.lastMode)+L"清主基地区域；先敌军炮台，再外围水晶等建筑，清完后安全集结。";
    if(s=="applied" && d.lastKey==7)return std::wstring(L"已下令：")+scope(d.lastMode)+L"就近警戒迎敌；忽略探机，再按7更新位置；等反隐时仍可安全交战。";
    if(s=="applied" && d.lastKey==8)return std::wstring(L"已下令：")+scope(d.lastMode)+(d.lastSafeRally?
        L"安全撤离集结；先脱险再汇合，等待下一条指令。":L"原地等援；受压共同避险、安全后归位；9撤离集结。");
    if(s=="applied" && d.lastKey==9)return L"全军回防：分守主矿和分矿，忽略探机诱导；再按0重分配。";
    if(s=="applied")return std::wstring(L"已下令：")+scope(d.lastMode)+L" · "+action(d.lastKey)+L"（"+std::to_wstring(d.commandUnits)+L"名）。";
    return L"1避险；2采矿；3/4选范围；8原地等援；9撤离集结；0回防；-重置。";
}
inline std::array<std::wstring,4> lines(const Data& d) {
    return {{
        L"1 探机避险   2 强制采矿   3 小队模式   4 全队模式   - 全局重置",
        L"5 近敌/分矿  6 主基地清场  7 区域警戒  8 原地集结  9 撤离集结  0 回防",
        std::wstring(scope(d.mode))+(d.mode==CommanderOrders::Scope::Squad?L"（"+std::to_wstring(d.selected)+L"）":L"")+L"｜作战部队："+std::to_wstring(d.army)+L"｜主力："+d.mainTask+L"｜小队："+d.squadTask+L"｜矿锁："+(d.miningLocked?L"开":L"关"),
        L"提示："+(d.notice.empty()?feedback(d):d.notice)
    }};
}
}
