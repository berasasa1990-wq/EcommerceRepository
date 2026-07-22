/**
 * Staff edit mode: 800×800 objava u stilu promo banera
 * (zelene mrlje, AKCIJA, artikal, SAMO cijena / snizena).
 */
(function () {
    'use strict';

    const SIZE = 800;
    const GREEN = '#5BB805';
    const GREEN_DARK = '#3d8a04';
    const GREEN_DEEP = '#2f6b03';
    const GREEN_LIGHT = '#7ed321';
    const BLACK = '#111111';
    const RED = '#e11d48';

    function $(id) {
        return document.getElementById(id);
    }

    function parsePrice(raw) {
        const n = parseFloat(String(raw || '0').replace(',', '.'));
        return Number.isFinite(n) && n > 0 ? n : 0;
    }

    function priceParts(n) {
        const v = Math.round(n * 100) / 100;
        const [intPart, decPart] = v.toFixed(2).split('.');
        return { intPart, decPart, text: intPart + ',' + decPart + ' KM' };
    }

    function formatPrice(n) {
        return priceParts(n).text;
    }

    function hexAlpha(hex, a) {
        const h = hex.replace('#', '');
        const r = parseInt(h.slice(0, 2), 16);
        const g = parseInt(h.slice(2, 4), 16);
        const b = parseInt(h.slice(4, 6), 16);
        return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a))})`;
    }

    function roundRect(ctx, x, y, w, h, r) {
        const radius = Math.min(r, w / 2, h / 2);
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.arcTo(x + w, y, x + w, y + h, radius);
        ctx.arcTo(x + w, y + h, x, y + h, radius);
        ctx.arcTo(x, y + h, x, y, radius);
        ctx.arcTo(x, y, x + w, y, radius);
        ctx.closePath();
    }

    function loadImage(url) {
        return new Promise((resolve) => {
            if (!url) {
                resolve(null);
                return;
            }
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => resolve(img);
            img.onerror = () => resolve(null);
            img.src = url;
        });
    }

    function wrapText(ctx, text, maxWidth) {
        const words = String(text || '').split(/\s+/).filter(Boolean);
        const lines = [];
        let line = '';
        words.forEach((word) => {
            const test = line ? `${line} ${word}` : word;
            if (ctx.measureText(test).width > maxWidth && line) {
                lines.push(line);
                line = word;
            } else {
                line = test;
            }
        });
        if (line) lines.push(line);
        return lines.slice(0, 2);
    }

    /** Deterministički pseudo-random (stabilne mrlje svaki put) */
    function mulberry32(seed) {
        let a = seed >>> 0;
        return function () {
            a |= 0;
            a = (a + 0x6d2b79f5) | 0;
            let t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    /** Meka eliptična mrlja (kao prolivena boja) */
    function softBlob(ctx, x, y, rx, ry, color, alpha) {
        ctx.save();
        ctx.translate(x, y);
        ctx.scale(rx, ry);
        const g = ctx.createRadialGradient(0, 0, 0.05, 0, 0, 1);
        g.addColorStop(0, hexAlpha(color, alpha));
        g.addColorStop(0.45, hexAlpha(color, alpha * 0.85));
        g.addColorStop(0.78, hexAlpha(color, alpha * 0.35));
        g.addColorStop(1, hexAlpha(color, 0));
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(0, 0, 1, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }

    /**
     * Prolivena / prosuta boja — velika mrlja + rivulets + raspršene kapi
     * inwardDir: ugao ka unutra (odakle se "proliva" na platno)
     */
    function paintSpill(ctx, cx, cy, scale, seed, inwardDir) {
        const rand = mulberry32(seed || 1);
        const dir = inwardDir != null ? inwardDir : 0;
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(dir);
        ctx.scale(scale, scale);

        // Gusti "bazen" prolivene boje (nepravilno, slojeno)
        softBlob(ctx, 0, 0, 95, 70, GREEN, 0.98);
        softBlob(ctx, 18, -8, 70, 55, GREEN_LIGHT, 0.7);
        softBlob(ctx, -20, 12, 55, 48, GREEN_DARK, 0.85);
        softBlob(ctx, 10, 18, 80, 40, GREEN, 0.75);

        // Nepravilni "jezik" tečenja ka unutra (duž +x lokalno)
        for (let i = 0; i < 8; i++) {
            const t = i / 8;
            const lx = 40 + t * (90 + rand() * 50);
            const ly = (rand() - 0.5) * 55 * (1 - t * 0.4);
            softBlob(ctx, lx, ly, 28 + rand() * 22, 14 + rand() * 16, rand() > 0.4 ? GREEN : GREEN_DARK, 0.7 + rand() * 0.25);
        }

        // Kapljice koje su "prsnule" okolo
        for (let i = 0; i < 55; i++) {
            const ang = rand() * Math.PI * 2;
            // Više kapi u smjeru tečenja
            const bias = Math.cos(ang) > 0 ? 1.4 : 0.7;
            const dist = (25 + rand() * 150) * bias;
            const rr = 1 + rand() * 9;
            const px = Math.cos(ang) * dist;
            const py = Math.sin(ang) * dist * (0.7 + rand() * 0.5);
            ctx.beginPath();
            ctx.ellipse(px, py, rr, rr * (0.6 + rand() * 0.5), ang, 0, Math.PI * 2);
            ctx.fillStyle = hexAlpha(rand() > 0.3 ? GREEN : GREEN_DARK, 0.65 + rand() * 0.35);
            ctx.fill();
        }

        // Dugi "mlazevi" / streakovi kao da je prosuto
        for (let i = 0; i < 14; i++) {
            const baseAng = (rand() - 0.5) * 1.1; // uglavnom ka unutra (+x)
            const len = 50 + rand() * 110;
            const thick = 2 + rand() * 6;
            ctx.save();
            ctx.rotate(baseAng);
            ctx.beginPath();
            ctx.moveTo(20, 0);
            ctx.bezierCurveTo(
                len * 0.3, (rand() - 0.5) * 18,
                len * 0.65, (rand() - 0.5) * 22,
                len, (rand() - 0.5) * 8
            );
            ctx.lineWidth = thick;
            ctx.strokeStyle = hexAlpha(rand() > 0.5 ? GREEN : GREEN_LIGHT, 0.75 + rand() * 0.25);
            ctx.lineCap = 'round';
            ctx.stroke();
            // kap na kraju
            ctx.beginPath();
            ctx.arc(len + 3, 0, thick * 0.85, 0, Math.PI * 2);
            ctx.fillStyle = GREEN;
            ctx.fill();
            // sitne kapi uz streak
            for (let j = 0; j < 4; j++) {
                const t = 0.3 + rand() * 0.6;
                ctx.beginPath();
                ctx.arc(len * t, (rand() - 0.5) * 12, 1 + rand() * 3, 0, Math.PI * 2);
                ctx.fillStyle = hexAlpha(GREEN, 0.7);
                ctx.fill();
            }
            ctx.restore();
        }

        // Sitna magla kapi daleko
        for (let i = 0; i < 30; i++) {
            const ang = rand() * Math.PI * 2;
            const dist = 80 + rand() * 100;
            ctx.beginPath();
            ctx.arc(Math.cos(ang) * dist, Math.sin(ang) * dist, 0.6 + rand() * 2.2, 0, Math.PI * 2);
            ctx.fillStyle = hexAlpha(GREEN, 0.45 + rand() * 0.4);
            ctx.fill();
        }

        ctx.restore();
    }

    function drawFrameSplashes(ctx, W, H) {
        // Uglovi — prosuto ka unutra
        paintSpill(ctx, -10, -10, 1.2, 101, Math.PI / 4);           // TL → unutra
        paintSpill(ctx, W + 10, -5, 1.35, 202, Math.PI * 0.75);      // TR
        paintSpill(ctx, -5, H + 10, 1.25, 303, -Math.PI / 4);        // BL
        paintSpill(ctx, W + 8, H + 8, 1.3, 404, -Math.PI * 0.75);    // BR
        // Strane
        paintSpill(ctx, W / 2, -25, 1.15, 505, Math.PI / 2);         // top
        paintSpill(ctx, W / 2, H + 25, 1.15, 606, -Math.PI / 2);     // bottom
        paintSpill(ctx, -25, H * 0.4, 1.1, 707, 0);                  // left
        paintSpill(ctx, W + 25, H * 0.55, 1.15, 808, Math.PI);       // right
        // Manji "prosuti" detalji uz rub
        paintSpill(ctx, 40, 140, 0.48, 901, 0.3);
        paintSpill(ctx, W - 40, 150, 0.45, 912, Math.PI - 0.3);
        paintSpill(ctx, 45, H - 130, 0.5, 923, -0.2);
        paintSpill(ctx, W - 45, H - 140, 0.48, 934, Math.PI + 0.25);
    }

    function blackBrush(ctx, x, y, w, h) {
        ctx.save();
        ctx.translate(x, y);
        ctx.beginPath();
        ctx.moveTo(0, h * 0.35);
        ctx.bezierCurveTo(w * 0.1, -h * 0.15, w * 0.3, h * 0.08, w * 0.55, h * 0.12);
        ctx.bezierCurveTo(w * 0.8, 0.05 * h, w * 0.95, -0.08 * h, w, h * 0.38);
        ctx.bezierCurveTo(w * 0.97, h * 1.05, w * 0.7, h * 0.95, w * 0.4, h * 0.9);
        ctx.bezierCurveTo(w * 0.15, h * 1.02, w * 0.03, h * 0.82, 0, h * 0.68);
        ctx.closePath();
        ctx.fillStyle = BLACK;
        ctx.fill();
        ctx.restore();
    }

    function greenBrush(ctx, x, y, w, h) {
        ctx.save();
        ctx.translate(x, y);
        ctx.beginPath();
        ctx.moveTo(0, h * 0.4);
        ctx.bezierCurveTo(w * 0.12, -h * 0.1, w * 0.4, h * 0.05, w * 0.6, h * 0.08);
        ctx.bezierCurveTo(w * 0.85, 0, w * 0.96, h * 0.3, w, h * 0.5);
        ctx.bezierCurveTo(w * 0.9, h * 1.05, w * 0.55, h * 0.95, w * 0.3, h * 0.9);
        ctx.bezierCurveTo(w * 0.1, h * 1.0, 0.02 * w, h * 0.75, 0, h * 0.6);
        ctx.closePath();
        const g = ctx.createLinearGradient(0, 0, w, 0);
        g.addColorStop(0, GREEN);
        g.addColorStop(1, GREEN_DARK);
        ctx.fillStyle = g;
        ctx.fill();
        ctx.restore();
    }

    function drawIconCircle(ctx, x, y, r, kind) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = GREEN;
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.fillStyle = '#fff';
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2.2;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        if (kind === 'badge') {
            // medalja
            ctx.beginPath();
            ctx.arc(x, y - 1, r * 0.38, 0, Math.PI * 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(x - r * 0.22, y + r * 0.15);
            ctx.lineTo(x - r * 0.35, y + r * 0.55);
            ctx.lineTo(x, y + r * 0.3);
            ctx.lineTo(x + r * 0.35, y + r * 0.55);
            ctx.lineTo(x + r * 0.22, y + r * 0.15);
            ctx.stroke();
        } else if (kind === 'truck') {
            roundRect(ctx, x - r * 0.4, y - r * 0.15, r * 0.55, r * 0.35, 2);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(x + r * 0.15, y - r * 0.05);
            ctx.lineTo(x + r * 0.45, y - r * 0.05);
            ctx.lineTo(x + r * 0.45, y + r * 0.2);
            ctx.lineTo(x + r * 0.15, y + r * 0.2);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(x - r * 0.2, y + r * 0.28, r * 0.12, 0, Math.PI * 2);
            ctx.arc(x + r * 0.25, y + r * 0.28, r * 0.12, 0, Math.PI * 2);
            ctx.fill();
        } else if (kind === 'shield') {
            ctx.beginPath();
            ctx.moveTo(x, y - r * 0.45);
            ctx.lineTo(x + r * 0.35, y - r * 0.25);
            ctx.lineTo(x + r * 0.35, y + r * 0.1);
            ctx.quadraticCurveTo(x, y + r * 0.5, x - r * 0.35, y + r * 0.1);
            ctx.lineTo(x - r * 0.35, y - r * 0.25);
            ctx.closePath();
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(x - r * 0.12, y);
            ctx.lineTo(x - 0.02 * r, y + r * 0.15);
            ctx.lineTo(x + r * 0.18, y - r * 0.12);
            ctx.stroke();
        } else if (kind === 'support') {
            ctx.beginPath();
            ctx.arc(x, y, r * 0.38, Math.PI * 0.15, Math.PI * 0.85);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(x - r * 0.28, y, r * 0.12, 0, Math.PI * 2);
            ctx.arc(x + r * 0.28, y, r * 0.12, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.moveTo(x - r * 0.15, y + r * 0.35);
            ctx.lineTo(x + r * 0.15, y + r * 0.35);
            ctx.stroke();
        }
        ctx.restore();
    }

    function drawBenefits(ctx, W) {
        const items = [
            { kind: 'badge', title: 'GARANCIJA', sub: '100% SIGURNOST\nKUPOVINE', emphasize: true },
            { kind: 'truck', title: 'BRZA DOSTAVA', sub: 'PO CIJELOJ BiH' },
            { kind: 'shield', title: 'SIGURNA', sub: 'KUPOVINA' },
            { kind: 'support', title: 'PODRŠKA', sub: '24/7' },
        ];

        // Bijela podloga desno (pokrije mrlje, ne dira krug cijene)
        const panelX = W - 148;
        const panelY = 220;
        const panelW = 148;
        const panelH = 400;
        ctx.save();
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(panelX, panelY, panelW, panelH);
        ctx.restore();

        const x = W - 74;
        const startY = 258;
        items.forEach((it, i) => {
            const y = startY + i * 90;
            const iconR = it.emphasize ? 24 : 20;

            drawIconCircle(ctx, x, y, iconR, it.kind);

            const padY = it.emphasize ? 34 : 32;
            ctx.fillStyle = BLACK;
            ctx.font = it.emphasize
                ? '900 12px "Segoe UI", Inter, system-ui, sans-serif'
                : '800 10px "Segoe UI", Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText(it.title, x, y + padY);

            if (it.sub) {
                ctx.fillStyle = it.emphasize ? GREEN_DEEP : '#444';
                ctx.font = it.emphasize
                    ? '700 9px "Segoe UI", Inter, system-ui, sans-serif'
                    : '700 8px "Segoe UI", Inter, system-ui, sans-serif';
                it.sub.split('\n').forEach((line, li) => {
                    ctx.fillText(line, x, y + padY + 14 + li * 11);
                });
            }
        });
    }

    function drawFooterBar(ctx, W, H) {
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(0, H - 78);
        ctx.quadraticCurveTo(W * 0.25, H - 102, W * 0.5, H - 86);
        ctx.quadraticCurveTo(W * 0.75, H - 102, W, H - 78);
        ctx.lineTo(W, H);
        ctx.lineTo(0, H);
        ctx.closePath();
        const g = ctx.createLinearGradient(0, H - 100, 0, H);
        g.addColorStop(0, GREEN);
        g.addColorStop(1, GREEN_DARK);
        ctx.fillStyle = g;
        ctx.fill();
        ctx.restore();

        // Sajt
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.font = '800 14px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.fillText('www.opremazaribolov.ba', W / 2, H - 58);

        ctx.font = '800 11px "Segoe UI", Inter, system-ui, sans-serif';
        const cols = [
            { t: 'PISANA GARANCIJA', s: 'UZ SVAKU NARUDŽBU' },
            { t: 'DOSTAVA PO BiH', s: 'SAMO 11,00 KM' },
            { t: 'OSIGURANJE PAKETA', s: 'NA LOM I OŠTEĆENJE' },
        ];
        cols.forEach((c, i) => {
            const x = W * (0.18 + i * 0.32);
            ctx.font = '800 11px "Segoe UI", Inter, system-ui, sans-serif';
            ctx.fillText(c.t, x, H - 32);
            ctx.font = '600 9px "Segoe UI", Inter, system-ui, sans-serif';
            ctx.fillText(c.s, x, H - 16);
        });
    }

    /** Stara cijena ravno i čitljiva; crvena crta ukoso preko */
    function drawStruckOldPrice(ctx, text, x, y) {
        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        // Cijena ravno — tamna da se lijepo vidi
        ctx.fillStyle = '#1e293b';
        ctx.font = '700 22px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.fillText(text, x, y);
        const w = ctx.measureText(text).width;
        // Jasnije ukosa crvena linija (ne prekriva cijeli broj)
        ctx.strokeStyle = RED;
        ctx.lineWidth = 2.6;
        ctx.lineCap = 'round';
        ctx.beginPath();
        // od donjeg-lijevog ka gornjem-desnom (ukoso)
        ctx.moveTo(x - w / 2 - 10, y + 10);
        ctx.lineTo(x + w / 2 + 10, y - 10);
        ctx.stroke();
        ctx.restore();
    }

    async function drawPost(canvas, { name, imageUrl, basePrice, percent }) {
        const ctx = canvas.getContext('2d');
        const W = SIZE;
        const H = SIZE;
        const hasPct = percent != null && percent > 0 && percent < 100;
        const salePrice = hasPct
            ? Math.max(0, Math.round(basePrice * (1 - percent / 100) * 100) / 100)
            : basePrice;
        const displayPrice = hasPct ? salePrice : basePrice;
        const parts = priceParts(displayPrice);

        // 1) Čisto bijela podloga (kao pozadina slike artikla)
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, W, H);

        // 2) Raspršene zelene mrlje samo uz rub (#5BB805)
        drawFrameSplashes(ctx, W, H);

        // Sajt gore
        ctx.fillStyle = GREEN_DEEP;
        ctx.font = '700 13px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('www.opremazaribolov.ba', W / 2, 22);

        // 3) VRHUNSKI IZBOR! badge
        ctx.save();
        roundRect(ctx, W / 2 - 130, 40, 260, 36, 18);
        ctx.fillStyle = '#ffffff';
        ctx.shadowColor = 'rgba(0,0,0,0.1)';
        ctx.shadowBlur = 8;
        ctx.fill();
        ctx.restore();
        ctx.strokeStyle = hexAlpha(GREEN, 0.45);
        ctx.lineWidth = 1.5;
        roundRect(ctx, W / 2 - 130, 40, 260, 36, 18);
        ctx.stroke();
        ctx.fillStyle = GREEN_DEEP;
        ctx.font = '800 16px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('VRHUNSKI IZBOR!', W / 2, 58);

        // 4) AKCIJA (veliki naslov)
        // Sunburst linije
        ctx.save();
        ctx.strokeStyle = hexAlpha(GREEN, 0.35);
        ctx.lineWidth = 3;
        for (let i = -4; i <= 4; i++) {
            if (i === 0) continue;
            const ang = -Math.PI / 2 + i * 0.12;
            ctx.beginPath();
            ctx.moveTo(W / 2 + Math.cos(ang) * 40, 128 + Math.sin(ang) * 20);
            ctx.lineTo(W / 2 + Math.cos(ang) * 120, 108 + Math.sin(ang) * 55);
            ctx.stroke();
        }
        ctx.restore();

        ctx.fillStyle = BLACK;
        ctx.font = '900 92px Impact, "Arial Black", "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'alphabetic';
        ctx.fillText('AKCIJA', W / 2, 162);

        // 5) Naziv artikla na crnom brush strokeu
        const nameUpper = String(name || 'ARTIKAL').toUpperCase();
        ctx.font = '800 22px "Segoe UI", Inter, system-ui, sans-serif';
        const nameLines = wrapText(ctx, nameUpper, 420);
        const brushW = Math.min(480, Math.max(280, ctx.measureText(nameLines[0] || '').width + 48));
        blackBrush(ctx, W / 2 - brushW / 2, 175, brushW, 44 + (nameLines.length - 1) * 22);
        ctx.fillStyle = '#ffffff';
        ctx.font = '800 20px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        nameLines.forEach((line, i) => {
            const words = line.split(' ');
            if (words.length >= 2) {
                const mid = Math.ceil(words.length / 2);
                const left = words.slice(0, mid).join(' ');
                const right = words.slice(mid).join(' ');
                ctx.font = '800 20px "Segoe UI", Inter, system-ui, sans-serif';
                const leftW = ctx.measureText(left + ' ').width;
                const rightW = ctx.measureText(right).width;
                const total = leftW + rightW;
                const startX = W / 2 - total / 2;
                ctx.textAlign = 'left';
                ctx.fillStyle = GREEN;
                ctx.fillText(left + ' ', startX, 205 + i * 24);
                ctx.fillStyle = '#ffffff';
                ctx.fillText(right, startX + leftW, 205 + i * 24);
                ctx.textAlign = 'center';
            } else {
                ctx.fillStyle = '#ffffff';
                ctx.fillText(line, W / 2, 205 + i * 24);
            }
        });

        // 6) Artikal — bijela na bijelo, bez sjene/okvira (ivice nestaju)
        const img = await loadImage(imageUrl);
        const imgBox = { x: 40, y: 230, w: 500, h: 380 };
        if (img) {
            // Contain, bez shadow-a da se bijela pozadina slike stopi sa canvasom
            const scale = Math.min(imgBox.w / img.naturalWidth, imgBox.h / img.naturalHeight);
            const dw = img.naturalWidth * scale;
            const dh = img.naturalHeight * scale;
            const dx = imgBox.x + (imgBox.w - dw) / 2;
            const dy = imgBox.y + (imgBox.h - dh) / 2;
            ctx.drawImage(img, dx, dy, dw, dh);
        } else {
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(imgBox.x, imgBox.y, imgBox.w, imgBox.h);
            ctx.fillStyle = '#94a3b8';
            ctx.font = '600 20px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Nema slike', imgBox.x + imgBox.w / 2, imgBox.y + imgBox.h / 2);
        }

        // 7) Benefit ikone desno (bijela podloga) — PRIJE kruga da krug bude cijeli gore
        drawBenefits(ctx, W);

        // 8) Cijena badge — kompletan krug, desno (ne prekriva artikal)
        // benefit panel od x≈652; footer od y~722
        const badgeR = 96;
        const badgeX = 555; // desnije od artikla; desni rub ≈ 651
        const badgeY = 450;

        ctx.save();
        ctx.beginPath();
        ctx.arc(badgeX, badgeY, badgeR, 0, Math.PI * 2);
        const bg = ctx.createRadialGradient(badgeX - 22, badgeY - 22, 8, badgeX, badgeY, badgeR);
        bg.addColorStop(0, GREEN_LIGHT);
        bg.addColorStop(0.5, GREEN);
        bg.addColorStop(1, GREEN_DARK);
        ctx.fillStyle = bg;
        ctx.shadowColor = 'rgba(47, 107, 3, 0.28)';
        ctx.shadowBlur = 16;
        ctx.fill();
        ctx.restore();

        // Kompletan bijeli prsten oko kruga
        ctx.beginPath();
        ctx.arc(badgeX, badgeY, badgeR - 3, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(255,255,255,0.45)';
        ctx.lineWidth = 4;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(badgeX, badgeY, badgeR, 0, Math.PI * 2);
        ctx.strokeStyle = hexAlpha(GREEN_DEEP, 0.25);
        ctx.lineWidth = 2;
        ctx.stroke();

        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        if (hasPct) {
            // Stara cijena IZNAD kruga (ravna) + ukosa crvena linija — potpuno vidljiva
            drawStruckOldPrice(ctx, formatPrice(basePrice), badgeX, badgeY - badgeR - 8);

            ctx.fillStyle = RED;
            ctx.font = '900 20px "Segoe UI", Inter, system-ui, sans-serif';
            ctx.fillText(`−${Number.isInteger(percent) ? percent : percent}%`, badgeX, badgeY - 36);
        }

        ctx.fillStyle = BLACK;
        ctx.font = '900 15px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.fillText('SAMO', badgeX, badgeY + (hasPct ? -10 : -32));

        // Velika cijena unutar kruga
        ctx.fillStyle = BLACK;
        ctx.font = '900 56px Impact, "Arial Black", "Segoe UI", sans-serif';
        const main = parts.intPart;
        const dec = parts.decPart;
        ctx.textAlign = 'left';
        const mainW = ctx.measureText(main).width;
        ctx.font = '900 26px Impact, "Arial Black", "Segoe UI", sans-serif';
        const decW = ctx.measureText(dec).width;
        ctx.font = '900 16px "Segoe UI", Inter, system-ui, sans-serif';
        const kmW = ctx.measureText('KM').width;
        const totalW = mainW + decW + 6 + kmW + 4;
        let px = badgeX - totalW / 2;
        const py = badgeY + (hasPct ? 22 : 8);

        ctx.font = '900 56px Impact, "Arial Black", "Segoe UI", sans-serif';
        ctx.fillStyle = BLACK;
        ctx.fillText(main, px, py);
        px += mainW;
        ctx.font = '900 26px Impact, "Arial Black", "Segoe UI", sans-serif';
        ctx.fillText(dec, px, py - 14);
        px += decW + 4;
        ctx.font = '900 16px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.fillText('KM', px, py - 2);

        ctx.textAlign = 'center';
        ctx.fillStyle = BLACK;
        ctx.font = '800 12px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.fillText(hasPct ? 'SNIŽENA CIJENA!' : 'ODLIČAN IZBOR!', badgeX, badgeY + (hasPct ? 55 : 48));

        // 9) Donji zeleni banner
        drawFooterBar(ctx, W, H);
    }

    function init() {
        const btn = $('staffObjavaBtn');
        const overlay = $('staffObjavaOverlay');
        if (!btn || !overlay) return;

        const closeBtn = $('staffObjavaClose');
        const pctInput = $('staffObjavaPct');
        const genBtn = $('staffObjavaGenerate');
        const downloadLink = $('staffObjavaDownload');
        const canvas = $('staffObjavaCanvas');
        const status = $('staffObjavaStatus');

        const productName = btn.dataset.productName || 'Artikal';
        const productSlug = btn.dataset.productSlug || 'artikal';
        const basePrice = parsePrice(btn.dataset.productPrice);
        const imageUrl = btn.dataset.productImage || '';

        function open() {
            overlay.hidden = false;
            document.body.classList.add('popup-open');
            if (pctInput) pctInput.value = '';
            if (downloadLink) downloadLink.hidden = true;
            if (status) {
                status.hidden = true;
                status.textContent = '';
            }
            if (canvas) {
                const c = canvas.getContext('2d');
                c.fillStyle = '#fff';
                c.fillRect(0, 0, SIZE, SIZE);
                c.fillStyle = GREEN;
                c.font = '700 18px system-ui, sans-serif';
                c.textAlign = 'center';
                c.fillText('Klikni „Generiši preview”', SIZE / 2, SIZE / 2);
            }
            setTimeout(() => pctInput && pctInput.focus(), 50);
        }

        function close() {
            overlay.hidden = true;
            document.body.classList.remove('popup-open');
        }

        function parsePct() {
            const raw = ((pctInput && pctInput.value) || '').trim().replace(',', '.');
            if (!raw) return null;
            const n = parseFloat(raw);
            if (!Number.isFinite(n) || n <= 0 || n >= 100) return null;
            return Math.round(n * 100) / 100;
        }

        async function generate() {
            if (!canvas) return;
            const percent = parsePct();
            if (status) {
                status.hidden = false;
                status.textContent = 'Generišem…';
            }
            if (genBtn) genBtn.disabled = true;
            try {
                await drawPost(canvas, {
                    name: productName,
                    imageUrl,
                    basePrice,
                    percent,
                });
                const dataUrl = canvas.toDataURL('image/png');
                if (downloadLink) {
                    const safe = (productSlug || 'objava').replace(/[^\w\-]+/g, '_');
                    const pctPart = percent != null ? `-${percent}pct` : '';
                    downloadLink.href = dataUrl;
                    downloadLink.download = `objava-${safe}${pctPart}-800x800.png`;
                    downloadLink.hidden = false;
                    downloadLink.textContent = 'Preuzmi PNG 800×800';
                }
                if (status) {
                    status.textContent = percent != null
                        ? `Spremno — AKCIJA −${percent}% (precrtana + SAMO snizena).`
                        : 'Spremno — AKCIJA, redovna cijena.';
                }
            } catch (err) {
                if (status) status.textContent = 'Greška pri generisanju. Pokušaj ponovo.';
                console.error(err);
            } finally {
                if (genBtn) genBtn.disabled = false;
            }
        }

        btn.addEventListener('click', (e) => {
            e.preventDefault();
            open();
        });
        closeBtn && closeBtn.addEventListener('click', close);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !overlay.hidden) close();
        });
        genBtn && genBtn.addEventListener('click', generate);
        pctInput && pctInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                generate();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
