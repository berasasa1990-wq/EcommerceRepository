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
        if(dialog.open || matchMedia('(min-width: 1025px)').matches)return;
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
        if (dialog.open || matchMedia('(min-width: 1025px)').matches) return;
        window.openMobileSearch();
    }
    headerInput?.addEventListener('focus', () => {
        if (!closing) openHeaderSearch();
    });
    // A click must also reopen the dialog when focus returned to this input on close.
    headerInput?.addEventListener('click', openHeaderSearch);
    dialog.addEventListener('click',e=>{if(e.target.closest('.mobile-home-dock button')&&!e.target.closest('[data-mobile-home-search]'))close();});
    function searchParams(query) {
        const params = new URLSearchParams();
        const current = new URLSearchParams(window.location.search);
        ['kategorija', 'na_stanju', 'brend', 'velicina', 'cijena_od', 'cijena_do', 'sort', 'akcija', 'noviteti', 'izdvojeno'].forEach(key => {
            if (current.get(key)) params.set(key, current.get(key));
        });
        const category = document.querySelector('#searchForm select[name="kategorija"]');
        if (category) { if (category.value) params.set('kategorija', category.value); else params.delete('kategorija'); }
        params.set('q', query);
        return params;
    }

    dialog.querySelector('form')?.addEventListener('submit', () => {
        const form = dialog.querySelector('form');
        const params = searchParams(input.value.trim());
        form.querySelectorAll('input[data-search-filter]').forEach(node => node.remove());
        params.forEach((value, key) => {
            if (key === 'q' || form.querySelector(`select[name="${key}"]`)) return;
            const node = document.createElement('input');
            node.type = 'hidden'; node.name = key; node.value = value; node.dataset.searchFilter = '1'; form.append(node);
        });
    });
    async function search(){
        controller?.abort();const current=++generation, query=input.value.trim();
        if(query.length<2){results.replaceChildren();all.hidden=true;related.hidden=true;status.textContent='Unesite najmanje dva znaka.';return;}
        controller=new AbortController();status.textContent='Pretražujemo…';
        try{
            const response=await fetch(`/api/pretraga/?${searchParams(query)}&limit=5`,{signal:controller.signal});
            if(!response.ok)throw new Error();const data=await response.json();if(current!==generation)return;
            status.textContent=data.results.length?'':'Nema proizvoda za ovu pretragu.';
            results.replaceChildren();
            const fragment=document.createDocumentFragment();
            data.results.forEach(item=>{
                const row=element('article','mobile-search-result');
                const link=element('a','mobile-search-result__main');link.href=item.url;
                if(item.image){const img=element('img','');img.src=item.image;img.alt='';img.loading='lazy';img.decoding='async';link.append(img);}
                link.append(element('strong','',item.naziv));
                row.append(link);
                const price=element('a','mobile-search-result__side');price.href=item.url;
                price.append(element('strong','',`${item.price.replace('.',',')} KM`));row.append(price);
                fragment.append(row);
            });
            results.append(fragment);
            all.href=`/?${searchParams(query)}#product-showcase`;all.hidden=!data.results.length;
            related.hidden=true;
        }catch(e){if(e.name!=='AbortError'&&current===generation)status.textContent='Pretraga trenutno nije dostupna. Pokušajte ponovo.';}
    }
    input.addEventListener('input',()=>{controller?.abort();generation++;clearTimeout(timer);timer=setTimeout(search,150);});
    matchMedia('(max-width: 1024px)').addEventListener('change',e=>{if(!e.matches&&dialog.open)close();});
})();
