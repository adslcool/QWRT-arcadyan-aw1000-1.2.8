'use strict';
'require view';
'require rpc';
'require poll';
'require ui';
var status=rpc.declare({object:'aw1000.internet',method:'status',reject:true,expect:{}});
var apply=rpc.declare({object:'aw1000.internet',method:'apply_priority',params:['priority'],reject:true,expect:{}});
var confirm=rpc.declare({object:'aw1000.internet',method:'confirm',reject:true,expect:{}});
return view.extend({
    handleSaveApply:null,handleSave:null,handleReset:null,
    load:function(){return status();},
    render:function(data){
        var labels={ready:'SIM 就绪',absent:'未插入 SIM',locked:'SIM 需要解锁',failed:'SIM 故障',unknown:'SIM 状态未知'};
        var priority=E('select',{class:'cbi-input-select'},[
            E('option',{value:'wired_only'},'仅有线网络'),
            E('option',{value:'cellular_only'},'仅SIM卡上网（3G/4G/5G）'),
            E('option',{value:'wired'},'有线网络优先，断网自动用SIM卡'),
            E('option',{value:'cellular'},'SIM卡上网优先，断网自动用有线')
        ]);
        priority.value=data.priority||'wired';
        var info=E('div',{}),details=E('div',{}),error=E('div',{class:'alert-message warning',style:'display:none'});
        function check(r){if(!r||r.ok!==true)throw new Error(r&&r.error||'操作失败');return r;}
        var retain=E('button',{class:'cbi-button cbi-button-positive',style:'display:none',click:function(){
            return confirm().then(check).then(function(){retain.style.display='none';}).catch(function(e){error.textContent=e.message;error.style.display='';});
        }},'确认保留当前设置');
        var save;
        function content(node,children){while(node.firstChild)node.removeChild(node.firstChild);children.forEach(function(c){node.appendChild(c);});}
        function health(s,up,turn){
            if(s==='disabled')return '停止检测';
            if(up&&(s==='offline'||!s||s==='unknown')&&Number(turn||0)<4)return '启动检测中';
            return {online:'正常',offline:'不可用',connecting:'恢复检测中',disconnecting:'故障检测中'}[s]||'未知';
        }
        function refresh(s){
            retain.style.display=s.pending?'':'none';
            retain.textContent='确认保留当前设置（剩余 '+(s.remaining||0)+' 秒）';
            if(save)save.disabled=!!s.pending;
            var unused='本模式不使用，已停止检测';
            content(info,[
                E('p',{},'当前上网线路：'+(s.selected==='wan'?'有线网络':s.selected?'SIM卡网络':'离线')),
                E('p',{},'有线网络：'+(s.priority==='cellular_only'?unused:(s.wired_up?'接口已连接 / ':'接口未连接 / ')+health(s.wan_health,s.wired_up,s.wan_turn))),
                E('p',{},'SIM卡网络：'+(s.priority==='wired_only'?unused:(s.cellular_up?'数据会话已连接 / ':'数据会话未连接 / ')+(s.blocked?'SIM 状态已排除该线路':health(s.cell_health,s.cellular_up,99)))+'；'+(labels[s.sim]||labels.unknown))
            ]);
            var v6=s.ipv6||{},sync=s.sync||{},dns=sync.dns||{},track=s.tracking||{},bytes=s.cellular_bytes||{},event=s.last||{};
            var v6text=v6.choice===s.selected?({native:'原生 IPv6',nat66:'ULA / NAT66',unavailable:'当前出口无可用 IPv6'}[v6.mode]||'等待同步'):'等待同步';
            var dnstext=s.dns_mode==='managed'?'自定义服务接管':s.dns_mode==='mixed'?'按实例分别管理':s.dns_mode==='auto'&&s.dns_choice===s.selected?'已跟随出口':'等待同步';
            if(dns.phase==='failed'||dns.phase==='retry')dnstext='应用失败，等待重试';
            function probe(p){p=p||{};return p.role==='off'?unused:({active:'当前检测',standby:'备用',recovery:'恢复检测'}[p.role]||'等待检测')+(p.interval?' / '+p.interval+' 秒':'')+'；累计 '+(p.attempts||0)+' 次';}
            content(details,[
                E('p',{},'DNS：'+dnstext+'；IPv6：'+v6text),
                E('p',{},'探测：有线网络 '+probe(track.wan)+'；SIM卡网络 '+probe(track.cellular)),
                E('p',{},'SIM卡本次接口流量：下载 '+(Number(bytes.rx||0)/1048576).toFixed(2)+' MiB；上传 '+(Number(bytes.tx||0)/1048576).toFixed(2)+' MiB（含业务流量，接口重建清零）'),
                E('p',{},'无线采样：'+({fresh:'有效',unknown:'暂时未知',expired:'样本过期'}[(s.radio||{}).status]||'等待采样')+'；距有效样本 '+((s.radio||{}).age||0)+' 秒'),
                E('p',{},'最近切换：'+(event.time||'暂无')+'；'+(event.reason||'')),
                E('p',{},'协调编号：'+(sync.generation||'等待同步')+'；状态更新：启动后 '+(sync.uptime||0)+' 秒')
            ]);
        }
        save=E('button',{class:'cbi-button cbi-button-apply',click:ui.createHandlerFn(this,function(){
            error.style.display='none';
            return apply(priority.value).then(check).then(function(r){
                if(r.unchanged)return;
                var remaining=r.timeout||120,timer;
                var message=E('p',{},'正在应用线路模式。网络可用后请确认保留，超时会自动回退。');
                var button=E('button',{class:'cbi-button cbi-button-positive',click:function(){
                    return confirm().then(check).then(function(){clearInterval(timer);ui.hideModal();}).catch(function(e){message.textContent=e.message;});
                }},'确认保留设置');
                ui.showModal('确认线路模式',[message,button]);
                timer=setInterval(function(){remaining--;message.textContent='剩余 '+remaining+' 秒自动回退。';if(remaining<=0){clearInterval(timer);ui.hideModal();}},1000);
            }).catch(function(e){error.textContent=e.message;error.style.display='';});
        })},'保存并应用');
        refresh(data);poll.add(function(){return status().then(refresh);},5);
        return E('div',{},[
            E('h2',{},'上网设置'),info,
            E('div',{class:'cbi-value'},[E('label',{class:'cbi-value-title'},'线路模式'),E('div',{class:'cbi-value-field'},priority)]),
            E('p',{},[save,' ',retain]),error,
            E('p',{},['有线拨号及其他接口参数请在 ',E('a',{href:L.url('admin/network/network')},'网络 → 接口'),' 中配置。']),
            E('details',{},[E('summary',{},'详细状态'),details,
                E('p',{},'备用SIM卡通常每 60 秒检测，故障退避至 120／300 秒；有线故障后唤醒，连续通过 4 轮检测再接管。仅有线或仅SIM卡模式停止另一线路探测，保留电话和模组注册。'),
                E('p',{},'DNS、IPv6跟随选中出口。原生IPv6使用运营商前缀；ULA仅在NAT66模式下提供外网回退。探测停止不等于蜂窝总流量为零，管理期间暂停硬件加速。')
            ])
        ]);
    }
});
