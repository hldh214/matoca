const key=document.body.dataset.merchantKey;
const tz=Intl.DateTimeFormat().resolvedOptions().timeZone||"Asia/Tokyo";
const baseHeaders={"X-Timezone":tz};
const list=document.querySelector("#shop-list");
const queue=document.querySelector("#current-queue");
const joinDialog=document.querySelector("#join-dialog");
const cancelDialog=document.querySelector("#cancel-dialog");
let data=null,filter="available",selected=null,counts={adult:2,child:0},submitting=false;
let waiting=[],waitingKnown=false,waitingRevision=0;
const safe=(value)=>{const node=document.createElement("span");node.textContent=value??"";return node.innerHTML};
const available=(shop)=>shop.is_issuable&&shop.is_open&&!shop.is_holiday&&!shop.is_suspended;
const status=(shop)=>shop.is_holiday?"休業":!shop.is_open?"営業時間外":available(shop)?"受付可能":"受付停止";
const estimate=(shop)=>!shop.waiting_time?"―":shop.waiting_time.is_more?`${shop.waiting_time.minutes}分以上`:`約${shop.waiting_time.minutes}分`;

function renderShops(){
  const query=document.querySelector("#shop-search").value.trim().toLowerCase();
  const shops=(data?.shops||[]).filter((shop)=>(filter==="all"||available(shop))&&`${shop.name} ${shop.sub_name||""} ${shop.address||""}`.toLowerCase().includes(query));
  if(!shops.length){list.innerHTML=`<p class="empty-state">${filter==="available"?"現在、受付可能な店舗はありません":"条件に一致する店舗はありません"}</p>`;return}
  const hasCurrentQueue=Boolean(waiting.length);
  list.innerHTML=shops.map((shop)=>`<article class="shop-row" data-id="${shop.id}"><div class="shop-identity">${shop.image_url?`<img src="${safe(shop.image_url)}" alt="" loading="lazy">`:""}<div><h3>${safe(shop.sub_name||shop.name)}</h3><p>${safe(shop.address||shop.name)}</p></div></div><span class="status-pill ${available(shop)?"available":""}">${status(shop)}</span><span class="metric"><strong>${shop.current_waiting??0}</strong>組</span><span class="metric"><strong>${estimate(shop)}</strong></span><button class="join-button" type="button" ${available(shop)&&waitingKnown&&!hasCurrentQueue?"":"disabled"}>${!waitingKnown?"順番待ちを確認中":hasCurrentQueue?"順番待ち受付中":available(shop)?"今すぐ順番待ち":"受付できません"}</button></article>`).join("");
  list.querySelectorAll(".join-button:not(:disabled)").forEach((button)=>button.addEventListener("click",()=>openJoin(Number(button.closest(".shop-row").dataset.id))));
}
function renderQueue(){
  if(!waitingKnown){queue.textContent="順番待ちを確認中です";return}
  const item=waiting[0];
  if(!item){queue.textContent="現在の順番待ちはありません";return}
  const shop=data?.shops.find((value)=>String(value.id)===String(item.shop_id));
  queue.innerHTML=`<div class="active-queue"><strong>${safe(shop?.sub_name||shop?.name||"受付中")}</strong><div class="queue-metrics"><span>受付番号<b>${item.number??"―"}</b></span><span>前の組数<b>${item.count??"―"}組</b></span><span>目安時間<b>${estimate(item.waiting_time?item:shop||{})}</b></span></div><button class="text-button" id="cancel-button" type="button">取消</button></div>`;
  document.querySelector("#cancel-button").addEventListener("click",()=>{document.querySelector("#cancel-detail").textContent=`受付番号 ${item.number??item.id}`;cancelDialog.dataset.waitingId=item.id;cancelDialog.showModal()});
}
async function refresh(force=false){
  try{const response=await fetch(`/api/merchants/${key}/${force?"refresh":"snapshot"}`,{method:force?"POST":"GET",headers:baseHeaders});if(!response.ok)throw new Error();data=await response.json();renderShops();renderQueue();document.querySelector("#updated-at").textContent=`最終更新 ${new Date(data.refreshed_at).toLocaleTimeString("ja-JP",{hour:"2-digit",minute:"2-digit"})}${data.stale?"・保存済みデータを表示中":""}`}
  catch{if(!data)list.innerHTML='<p class="empty-state">最新情報を取得できませんでした</p>';document.querySelector("#updated-at").textContent="最新情報を取得できませんでした"}
}
function renderConfirmItems(){const target=document.querySelector("#confirm-items"),items=(selected?.forms?.confirm_items||[]).filter((item)=>item.enable);target.innerHTML=items.map((item,index)=>`<label>${safe(item.title)}<select data-answer="${index+1}">${(item.sub_items||[]).filter((option)=>option.enable&&!option.disabled).map((option)=>`<option value="${option.sub_item_index}">${safe(option.text)}</option>`).join("")}</select></label>`).join("")}
function openJoin(id){selected=data.shops.find((shop)=>shop.id===id);const forms=selected.forms||{};counts={adult:Math.max(forms.min_adult??0,Math.min(forms.max_adult??20,2)),child:Math.max(forms.min_child??0,Math.min(forms.max_child??20,0))};document.querySelector("#join-shop-name").textContent=selected.sub_name||selected.name;document.querySelector("#join-status").textContent=`${selected.current_waiting}組待ち・${estimate(selected)}`;document.querySelector("#adult-count").textContent=counts.adult;document.querySelector("#child-count").textContent=counts.child;document.querySelector("#join-error").textContent="";renderConfirmItems();joinDialog.showModal()}
function setFormBusy(form,busy){form.setAttribute("aria-busy",String(busy));form.querySelectorAll("button,select,input").forEach((control)=>{control.disabled=busy})}
document.querySelectorAll(".dialog-close").forEach((button)=>button.addEventListener("click",()=>button.closest("dialog").close()));
document.querySelectorAll("[data-filter]").forEach((button)=>button.addEventListener("click",()=>{filter=button.dataset.filter;document.querySelectorAll("[data-filter]").forEach((item)=>item.classList.toggle("is-active",item===button));renderShops()}));
document.querySelector("#shop-search").addEventListener("input",renderShops);
document.querySelector("#refresh-button").addEventListener("click",()=>refresh(true));
document.querySelectorAll("[data-step]").forEach((button)=>button.addEventListener("click",()=>{const type=button.dataset.step,forms=selected?.forms||{},min=forms[`min_${type}`]??0,max=forms[`max_${type}`]??20;counts[type]=Math.max(min,Math.min(max,counts[type]+Number(button.dataset.delta)));document.querySelector(`#${type}-count`).textContent=counts[type]}));
document.querySelector("#join-form").addEventListener("submit",async(event)=>{
  event.preventDefault();if(submitting)return;submitting=true;
  const form=event.currentTarget,answers=[...document.querySelectorAll("[data-answer]")].map((input)=>Number(input.value));
  setFormBusy(form,true);
  try{
    const response=await fetch(`/api/merchants/${key}/waiting`,{method:"POST",headers:{...baseHeaders,"Content-Type":"application/json"},body:JSON.stringify({shop_id:selected.id,adult_count:counts.adult,child_count:counts.child,answer1:answers[0]??0,answer2:answers[1]??null,in_advance_information:""})});
    if(!response.ok){const body=response.status===409?await response.json().catch(()=>({})):{};throw new Error(body.detail)}
    waiting=[await response.json()];waitingKnown=true;waitingRevision++;
    renderQueue();renderShops();joinDialog.close();
    await Promise.all([refreshWaiting(),refresh()]);
  }catch(error){document.querySelector("#join-error").textContent=error.message||"順番待ちの申し込みに失敗しました"}
  finally{submitting=false;setFormBusy(form,false)}
});
document.querySelector("#cancel-form").addEventListener("submit",async(event)=>{
  event.preventDefault();if(submitting)return;submitting=true;
  const form=event.currentTarget;setFormBusy(form,true);
  try{
    const response=await fetch(`/api/merchants/${key}/waiting/${cancelDialog.dataset.waitingId}`,{method:"DELETE",headers:baseHeaders});
    if(!response.ok){const body=response.status===409?await response.json().catch(()=>({})):{};throw new Error(body.detail)}
    waiting=[];waitingKnown=true;waitingRevision++;
    renderQueue();renderShops();cancelDialog.close();
    await Promise.all([refreshWaiting(),refresh()]);
  }catch(error){document.querySelector("#cancel-error").textContent=error.message||"順番待ちの取消に失敗しました"}
  finally{submitting=false;setFormBusy(form,false)}
});
setInterval(()=>{if(!document.hidden)refresh()},60000);
async function refreshWaiting(){
  const revision=++waitingRevision;
  try{
    const response=await fetch(`/api/merchants/${key}/waiting`,{headers:baseHeaders});
    if(!response.ok)throw new Error();
    const current=await response.json();
    if(revision!==waitingRevision)return;
    waiting=current;waitingKnown=true;renderQueue();renderShops();
  }catch{if(!waitingKnown)queue.textContent="順番待ちを確認できませんでした"}
}
setInterval(()=>{if(!document.hidden)refreshWaiting()},30000);
document.addEventListener("visibilitychange",()=>{if(!document.hidden){refresh();refreshWaiting()}});
refresh();
refreshWaiting();
