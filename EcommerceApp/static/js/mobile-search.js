(() => {
    const dialog = document.getElementById('mobileSearchDialog');
    if (!dialog) return;
    const input=dialog.querySelector('input'), results=dialog.querySelector('[data-search-results]'), status=dialog.querySelector('[data-search-status]');
    const all=dialog.querySelector('[data-search-all]'), related=dialog.querySelector('[data-search-related]');
    let controller, timer, dock, marker, generation=0, closing=false;
    const element=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;};
    function restore(){closing=true;setTimeout(()=>{closing=false;},150);if(dock?.parentElement===dialog)marker.after(dock);document.documentElement.classList.remove('mobile-search-open');controller?.abort();clearTimeout(timer);generation++;}
    function close(){closing=true;dialog.close();restore();}
    window.openMobileSearch=()=>{
        if(dialog.open)return;
        dock=matchMedia('(max-width: 1024px)').matches?document.querySelector('.mobile-home-dock'):null;
        if(dock){marker=document.createComment('search dock');dock.before(marker);dialog.append(dock);}
        dialog.showModal();document.documentElement.classList.add('mobile-search-open');input.focus();
        if(input.value.trim().length>=2)search();
    };
    dialog.addEventListener('close',restore);dialog.addEventListener('cancel',restore);
    dialog.querySelectorAll('[data-search-cancel]').forEach(b=>b.addEventListener('click',close));
    dialog.querySelector('[data-search-clear]').addEventListener('click',()=>{input.value='';input.dispatchEvent(new Event('input'));input.focus();});
    const headerInput = document.getElementById('searchInput');
    function openHeaderSearch() {
        if (dialog.open) return;
        if (matchMedia('(min-width: 1025px)').matches) input.value = headerInput.value;
        window.openMobileSearch();
    }
    headerInput?.addEventListener('focus', () => {
        if (!closing) openHeaderSearch();
    });
    // A click must also reopen the dialog when focus returned to this input on close.
    headerInput?.addEventListener('click', openHeaderSearch);
    dialog.addEventListener('click',e=>{if(e.target.closest('.mobile-home-dock button')&&!e.target.closest('[data-mobile-home-search]'))close();});
    async function search(){
        controller?.abort();const current=++generation, query=input.value.trim();results.replaceChildren();all.hidden=true;related.hidden=true;
        if(query.length<2){status.textContent='Unesite najmanje dva znaka.';return;}
        controller=new AbortController();status.textContent='Pretražujemo…';
        try{
            const response=await fetch(`/api/pretraga/?q=${encodeURIComponent(query)}&limit=4`,{signal:controller.signal});
            if(!response.ok)throw new Error();const data=await response.json();if(current!==generation)return;
            status.textContent=data.results.length?'':'Nema proizvoda za ovu pretragu.';
            const categories=new Map();
            data.results.forEach(item=>{
                const row=element('article','mobile-search-result');const link=element('a','mobile-search-result__main');link.href=item.url;
                const img=element('img','');img.src=item.image||'';img.alt='';if(item.image)link.append(img);
                const copy=element('div','');copy.append(element('small','',item.brand||''),element('strong','',item.naziv),element('small','',`Šifra: ${item.sifra||''}`),element('span',item.in_stock?'search-stock':'search-stock search-stock--out',item.in_stock?'● Na stanju':'Nije na stanju'));link.append(copy);row.append(link);
                const side=element('div','mobile-search-result__side');side.append(element('strong','',`${item.price.replace('.',',')} KM`));
                if(item.in_stock && item.variation_ids?.length<=1){
                    const add=element('button','','');add.type='button';add.setAttribute('aria-label',`Dodaj u korpu — ${item.naziv}`);add.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5" y="7" width="14" height="15" rx="2"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/></svg>';
                    add.addEventListener('click',async()=>{if(typeof window.catalogAddProductToCart!=='function'){location.href=item.url;return;}add.disabled=true;try{const r=await window.catalogAddProductToCart(item.slug,item.variation_ids[0]||'');if(r.requires_qty_deal_choice||r.requires_gratis_choice){close();return;}add.textContent='✓';status.textContent='Artikal je dodan u korpu.';}catch(e){status.textContent=e.message||'Dodavanje nije uspjelo.';}finally{add.disabled=false;}});side.append(add);
                }else{const more=element('a','mobile-search-result__more','›');more.href=item.url;more.setAttribute('aria-label',`Pogledaj ${item.naziv}`);side.append(more);}
                row.append(side);results.append(row);if(item.category)categories.set(item.category.url,item.category.name);
            });
            all.href=`/?q=${encodeURIComponent(query)}#product-showcase`;all.hidden=!data.results.length;
            related.querySelector('div').replaceChildren();categories.forEach((name,url)=>{const a=element('a','',name);a.href=url;related.querySelector('div').append(a);});related.hidden=!categories.size;
        }catch(e){if(e.name!=='AbortError'&&current===generation)status.textContent='Pretraga trenutno nije dostupna. Pokušajte ponovo.';}
    }
    input.addEventListener('input',()=>{controller?.abort();generation++;clearTimeout(timer);timer=setTimeout(search,220);});
    matchMedia('(max-width: 1024px)').addEventListener('change',e=>{if(!e.matches&&dialog.open)close();});
})();
