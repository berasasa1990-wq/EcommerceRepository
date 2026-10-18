const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = name => fs.readFileSync(`EcommerceApp/static/js/${name}.js`, 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment(beacon = true) {
  const events = {}, intervals = [], timers = [], calls = [];
  const root = {dataset:{},getAttribute:()=>'/poll/',classList:{add(){},remove(){}},contains:()=>false};
  const document = {hidden:false,title:'Test',getElementById:id=>['visitorPresenceRoot','publicOnlineRoot'].includes(id)?root:null,addEventListener:(event,fn)=>events[event]=fn};
  const context = {document, navigator:{sendBeacon:()=>beacon},URLSearchParams,FormData,Date,console,
    fetch:(url)=>{calls.push(url);return new Promise(resolve=>context.resolve=resolve);},
    window:{location:{pathname:'/',search:''},setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval(){},setTimeout:fn=>timers.push(fn),addEventListener:(event,fn)=>events[event]=fn}};
  context.setInterval=context.window.setInterval;context.clearInterval=context.window.clearInterval;
  return {context,events,intervals,timers,calls};
}

(async()=>{
  let e=environment();vm.runInNewContext(source('visitor-presence'),e.context);
  e.timers[0]();e.intervals[0]();assert.equal(e.calls.length,1,'heartbeat must not overlap');
  e.context.resolve({});await tick();e.context.document.hidden=true;e.intervals[0]();assert.equal(e.calls.length,1,'hidden heartbeat must stop');
  e.events.pagehide();assert.equal(e.calls.length,1,'accepted beacon must not also fetch');
  e=environment(false);vm.runInNewContext(source('visitor-presence'),e.context);e.events.pagehide();assert.equal(e.calls.length,1,'rejected beacon uses one fetch fallback');
  e=environment();vm.runInNewContext(source('public-online-visitors'),e.context);e.intervals[0]();assert.equal(e.calls.length,1,'public polling must not overlap');
  e.context.resolve({ok:true,status:200,json:async()=>({items:[],count:0})});await tick();e.context.document.hidden=true;e.intervals[0]();assert.equal(e.calls.length,1,'hidden public poll must stop');
  const main=source('main');const start=main.indexOf('    let searchGeneration = 0;');const end=main.indexOf('    function openSearchOverlay()',start);
  let resolve, renders=0;const timers=new Map();let next=0;
  const context={AbortController,searchFetchController:null,searchDebounceTimer:null,searchInput:{value:'fox'},searchSuggestions:{hidden:true},searchSuggestUrl:'/api/pretraga/',searchSuggestLimit:()=>4,
    renderSearchSuggestions:()=>renders++,clearSearchSuggestions(){},setSuggestionsExpanded(){},setSearchSuggestActive(){},positionSearchSuggestions(){},fetch:()=>new Promise(r=>resolve=r)};
  context.window={setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)};context.clearTimeout=context.window.clearTimeout;
  vm.createContext(context);vm.runInContext(main.slice(start,end),context);
  const pending=vm.runInContext("fetchSearchSuggestions('fox')",context);
  context.searchInput.value='fox new';vm.runInContext('queueSearchSuggestions()',context);
  resolve({ok:true,json:async()=>({results:[{}],query:'fox'})});await pending;
  assert.equal(renders,0,'obsolete autocomplete response must not render');
  assert.equal(timers.size,1,'only the next debounce timer remains');
  console.log('PASS: heartbeat overlap, hidden tabs, beacon fallback, public poll, stale autocomplete');
})().catch(error=>{console.error(error);process.exitCode=1;});
