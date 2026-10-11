const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync('EcommerceApp/template/bera_reference/staff/wms/location_detail.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
let clones=0;
const dialogs = new Map();
for (const key of ['a','b']) {
 const select={dataset:{},appended:[],append(fragment){this.appended.push(fragment);}};
 dialogs.set(key,{select,opened:0,querySelector(){return select;},showModal(){this.opened++;}});
}
const template={content:{cloneNode(deep){assert(deep);clones++;return ['all destination options'];}}};
const scope={window:{},document:{getElementById(id){return id==='wmsDestinationOptions'?template:dialogs.get(id);}}};
vm.runInNewContext(script,scope);
scope.window.openWmsLocationTransfer('a');scope.window.openWmsLocationTransfer('a');scope.window.openWmsLocationTransfer('b');
assert.equal(clones,2);assert.equal(dialogs.get('a').select.appended.length,1);assert.equal(dialogs.get('a').opened,2);
console.log('PASS: all destination options cloned once per opened dialog; reopening does not duplicate options');
