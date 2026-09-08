(function () {
    'use strict';
    function init() {
        var form = document.getElementById('akcija_form'), tip = document.getElementById('id_tip');
        if (!form || !tip) return;
        var intro = document.createElement('section');
        intro.className = 'ai-popup-intro';
        intro.innerHTML = '<span>AI PONUDE</span><h2>Jednostavno podešavanje ponuda</h2><p>Izaberi način prikazivanja i artikle. Kupcu se prikazuje jedan artikal po ponudi; naredni put drugi.</p><p class="ai-popup-summary" role="status"></p>';
        form.insertBefore(intro,form.firstChild);
        function selected(name) { var el=form.querySelector('[name="'+name+'"]:checked');return el ? el.value : ''; }
        var discount=document.getElementById('id_browse_interest_popust');
        var previousDiscount=discount && Number(discount.value)>0 ? discount.value : '10';
        var previousMode=selected('browse_interest_mode');
        function update(event) {
            var ai=tip.value==='ai_prodaja', mode=selected('browse_interest_mode'), source=selected('browse_interest_source');
            intro.hidden=!ai;
            document.body.classList.toggle('ai-popup-settings',ai);
            document.body.classList.toggle('ai-popup-category',ai && source==='category');
            document.body.classList.toggle('ai-popup-no-discount',ai && mode==='no_discount');
            if (!ai) return;
            if (discount && event && event.target.name==='browse_interest_mode') {
                if(mode==='no_discount'){previousDiscount=discount.value || '10';discount.value='0';}
                else if(previousMode==='no_discount') discount.value=previousDiscount;
            }
            previousMode=mode;
            var active=document.getElementById('id_browse_interest_popup_aktivan');
            var tempo=mode==='assertive' ? '35 s do prve ponude · 60 s razmaka · najviše 6 ponuda' : '120 s do prve ponude · 180 s razmaka · najviše 3 ponude';
            intro.querySelector('.ai-popup-summary').textContent=(active && active.checked ? 'Uključeno' : 'Isključeno')+' · '+tempo+' po posjeti. '+(mode==='no_discount' ? 'Redovne cijene, bez dodatnog popusta.' : 'Popust podesi ispod.');
        }
        form.addEventListener('change',update);update();
    }
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
