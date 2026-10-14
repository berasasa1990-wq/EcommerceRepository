(function () {
    var form = document.getElementById('mgOrderForm');
    if (!form) return;
    function dialog(title, wide) {
        var modal = document.createElement('div');
        modal.className = 'mg-modal blue-order-modal';
        modal.hidden = true;
        var bg = document.createElement('button');
        bg.type = 'button'; bg.className = 'mg-modal-bg'; bg.setAttribute('aria-label', 'Zatvori');
        var panel = document.createElement('section');
        panel.className = 'mg-modal-panel' + (wide ? ' blue-catalog-panel' : '');
        panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-label', title);
        var head = document.createElement('header'); head.className = 'blue-modal-head';
        var h = document.createElement('h2'); h.textContent = title;
        var close = document.createElement('button'); close.type = 'button'; close.className = 'mg-btn'; close.textContent = '×'; close.setAttribute('aria-label', 'Zatvori');
        head.append(h, close); panel.append(head); modal.append(bg, panel); form.append(modal);
        var previous;
        function hide() { modal.hidden = true; if (previous) previous.focus(); }
        bg.addEventListener('click', hide); close.addEventListener('click', hide);
        modal.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.preventDefault(); hide(); }
            if (e.key !== 'Tab') return;
            var nodes = Array.from(panel.querySelectorAll('button,input,a[href],select,textarea')).filter(function(n){return !n.disabled && n.getClientRects().length;});
            var first = nodes[0], last = nodes[nodes.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        });
        return {modal:modal,panel:panel,show:function(input){previous=document.activeElement;modal.hidden=false;if(input)input.focus();},hide:hide};
    }
    var picker = dialog('Izaberi kupca');
    picker.modal.classList.add('customer-choice-modal');
    var wrap = document.getElementById('mgCustomerSearchWrap');
    var search = document.getElementById('mgCustomerSearch');
    var list = document.getElementById('mgCustomerSuggest');
    var add = document.getElementById('mgCustomerAdd');
    picker.panel.append(wrap);
    search.placeholder = 'Pretraži ime, telefon ili grad';
    var newCustomer = document.createElement('button'); newCustomer.type='button'; newCustomer.className='mg-btn blue-new-customer'; newCustomer.setAttribute('aria-label','Dodaj kupca'); newCustomer.title='Dodaj kupca';
    newCustomer.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="9" cy="6" r="3"/><path d="M2 21v-3a7 7 0 0 1 14 0M19 7v8m-4-4h8"/></svg>';
    picker.panel.querySelector('header').insertBefore(newCustomer, picker.panel.querySelector('header').lastChild);
    newCustomer.addEventListener('click',function(){picker.hide();add.click();});
    var choose = document.getElementById('mgChooseCustomer');
    function openCustomer(){wrap.hidden=false;picker.show(search);search.dispatchEvent(new Event('input',{bubbles:true}));}
    choose.addEventListener('click',openCustomer);
    document.getElementById('mgCustomerChange').addEventListener('click',openCustomer);
    new MutationObserver(function(){if(wrap.hidden){picker.hide();choose.hidden=true;}}).observe(wrap,{attributes:true,attributeFilter:['hidden']});
    var mainSearch = document.getElementById('mgOrderSearch');
    var catalog = document.getElementById('mgOrderCatalog');
    var catalogDialog = dialog('Kataloški unos artikala', true);
    catalogDialog.modal.classList.add('catalog-choice-modal');
    var catalogSearch = document.createElement('input');
    catalogSearch.type='search';catalogSearch.placeholder='Pretraži naziv ili šifru';
    catalogSearch.setAttribute('aria-label','Pretraži katalog');
    catalogDialog.panel.append(catalogSearch, catalog);
    catalogSearch.addEventListener('keydown',function(event){if(event.key==='Enter')event.preventDefault();});
    catalogSearch.addEventListener('input',function(){
        mainSearch.value=catalogSearch.value;
        mainSearch.dispatchEvent(new Event('input',{bubbles:true}));
    });
    document.getElementById('mgOrderCatalogBtn').addEventListener('click',function(){
        if(!catalog.hidden){catalogSearch.value=mainSearch.value;catalogDialog.show(catalogSearch);}
        else catalogDialog.hide();
    });
    new MutationObserver(function(){
        if(catalogDialog.modal.hidden && !catalog.hidden)document.getElementById('mgOrderSoloBtn').click();
    }).observe(catalogDialog.modal,{attributes:true,attributeFilter:['hidden']});
    var quantityModal = document.getElementById('mgOrderQtyModal');
    if(quantityModal)new MutationObserver(function(){
        if(quantityModal.hidden && !catalogDialog.modal.hidden)catalogSearch.focus();
    }).observe(quantityModal,{attributes:true,attributeFilter:['hidden']});

    var suggestions = document.getElementById('mgOrderSuggest');
    if (suggestions) new MutationObserver(function(){
        mainSearch.setAttribute('aria-expanded', suggestions.hidden ? 'false' : 'true');
    }).observe(suggestions,{attributes:true,attributeFilter:['hidden']});
    if(list) {
        list.classList.add('blue-customer-list');
        new MutationObserver(function(){
            list.querySelectorAll('li:not(.is-empty)').forEach(function(row){
                row.tabIndex=0;row.setAttribute('role','button');
                if(row.dataset.keyboardReady)return;
                row.dataset.keyboardReady='1';
                row.addEventListener('keydown',function(event){if(event.key==='Enter'||event.key===' '){event.preventDefault();row.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));}});
            });
        }).observe(list,{childList:true});
    }
    if(document.getElementById('id_customer_id').value)choose.hidden=true;
    // Keep operational controls available under the additional options.
    var options = form.querySelector('.bera-order-options');
    var advanced = document.createElement('div'); advanced.className='bera-advanced-actions';
    ['mgSpareBtn','mgOrderBulk','mgOrderClear'].forEach(function(id){var node=document.getElementById(id);if(node)advanced.append(node);});
    form.querySelectorAll('.mg-order-foot-actions > :not([value="sacuvaj"])').forEach(function(node){advanced.append(node);});
    options.append(advanced);
    function discountTotal(){
        var subtotal=0;
        form.querySelectorAll('[data-line]').forEach(function(row){var q=row.querySelector('[name="kolicina"]');subtotal+=(parseFloat(row.dataset.cijena)||0)*(parseFloat(q && q.value)||0);});
        var goods=parseFloat(document.getElementById('mgOrderGoods').textContent.replace(/[^0-9,.-]/g,'').replace(',','.'))||0;
        document.getElementById('beraDiscountTotal').textContent='− '+Math.max(0,subtotal-goods).toFixed(2).replace('.',',')+' KM';
    }
    new MutationObserver(discountTotal).observe(document.getElementById('mgOrderGoods'),{childList:true,characterData:true,subtree:true});
    discountTotal();

})();
