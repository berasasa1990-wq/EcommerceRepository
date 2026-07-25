/**
 * Staff edit mode: 800×800 objava za društvene mreže.
 * - Akcija: template AKCIJA PONUDA + artikal, % sa strana, precrtana/crvena cijena
 * - Regularno: template NOVO PONUDA + naziv, veliki artikal, cijena ispod (bez %)
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

    /**
     * Ponovo nacrta rub templatea PREKO artikla da bijela pozadina slike
     * ne prekrije zelene mrlje. "Rupa" u sredini ostaje za artikal.
     */
    function compositeTemplateFrameOver(ctx, template, W, H, hole) {
        if (!template) {
            drawFrameSplashes(ctx, W, H);
            return;
        }
        const off = document.createElement('canvas');
        off.width = W;
        off.height = H;
        const octx = off.getContext('2d');
        octx.drawImage(template, 0, 0, W, H);
        // izreži središte (malo manje od hole da mrlje pređu preko rubova slike)
        const padX = Math.max(8, Math.round((hole.w || 0) * 0.06));
        const padY = Math.max(8, Math.round((hole.h || 0) * 0.06));
        const hx = (hole.x || 0) + padX;
        const hy = (hole.y || 0) + padY;
        const hw = Math.max(40, (hole.w || W) - padX * 2);
        const hh = Math.max(40, (hole.h || H) - padY * 2);
        octx.globalCompositeOperation = 'destination-out';
        octx.fillStyle = '#000';
        // blago zaobljeni cutout
        const r = 24;
        octx.beginPath();
        octx.moveTo(hx + r, hy);
        octx.arcTo(hx + hw, hy, hx + hw, hy + hh, r);
        octx.arcTo(hx + hw, hy + hh, hx, hy + hh, r);
        octx.arcTo(hx, hy + hh, hx, hy, r);
        octx.arcTo(hx, hy, hx + hw, hy, r);
        octx.closePath();
        octx.fill();
        octx.globalCompositeOperation = 'source-over';
        ctx.drawImage(off, 0, 0);
    }

    function getCsrfToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (match) return decodeURIComponent(match[1]);
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input && input.value ? input.value : '';
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

    /**
     * Akcija objava — template (zelene mrlje, AKCIJA PONUDA, prazno središte, CIJENA, % sa strana).
     * Layout skaliran na 800×800 prema originalu 1254×1254.
     */
    async function drawAkcijaFromTemplate(canvas, { name, imageUrl, basePrice, percent, templateUrl }) {
        const ctx = canvas.getContext('2d');
        const W = SIZE;
        const H = SIZE;
        const pct = percent != null && percent > 0 && percent < 100 ? percent : 0;
        const salePrice = pct
            ? Math.max(0, Math.round(basePrice * (1 - pct / 100) * 100) / 100)
            : basePrice;
        const pctLabel = Number.isInteger(pct) ? String(pct) : String(pct);

        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, W, H);

        const template = await loadImage(templateUrl);
        if (template) {
            ctx.drawImage(template, 0, 0, W, H);
        } else {
            // fallback ako template ne učita
            drawFrameSplashes(ctx, W, H);
            ctx.fillStyle = GREEN;
            ctx.font = '900 48px Impact, "Arial Black", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('AKCIJA PONUDA', W / 2, 90);
        }

        // --- Artikal prvo (naziv ide POSLIJE frame overlay-a da ne nestane) ---
        const nameUpper = String(name || 'ARTIKAL').toUpperCase();
        ctx.font = '900 26px "Segoe UI", Inter, system-ui, sans-serif';
        const nameLines3 = (() => {
            const words = String(nameUpper || '').split(/\s+/).filter(Boolean);
            const lines = [];
            let line = '';
            const maxW = W * 0.72;
            words.forEach((word) => {
                const test = line ? `${line} ${word}` : word;
                if (ctx.measureText(test).width > maxW && line) {
                    lines.push(line);
                    line = word;
                } else {
                    line = test;
                }
            });
            if (line) lines.push(line);
            return lines.slice(0, 3);
        })();
        const nameY0 = 198;
        const lineH = 30;
        const nameExtra = (nameLines3.length - 1) * 12;
        const imgBox = {
            x: 100,
            y: 255 + nameExtra,
            w: 600,
            h: 340 - nameExtra,
        };
        const img = await loadImage(imageUrl);
        let drawnImgRect = { ...imgBox };
        if (img) {
            const scale = Math.min(imgBox.w / img.naturalWidth, imgBox.h / img.naturalHeight) * 0.98;
            const dw = img.naturalWidth * scale;
            const dh = img.naturalHeight * scale;
            const dx = imgBox.x + (imgBox.w - dw) / 2;
            const dy = imgBox.y + (imgBox.h - dh) / 2;
            ctx.drawImage(img, dx, dy, dw, dh);
            drawnImgRect = { x: dx, y: dy, w: dw, h: dh };
        } else {
            ctx.fillStyle = '#94a3b8';
            ctx.font = '600 18px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Nema slike artikla', W / 2, imgBox.y + imgBox.h / 2);
        }

        // Mrlje / template rub PREKO bijele pozadine artikla
        compositeTemplateFrameOver(ctx, template, W, H, drawnImgRect);

        // Naziv (iznad artikla, poslije frame-a)
        const blockH = nameLines3.length * lineH + 12;
        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        roundRect(ctx, W * 0.12, nameY0 - 20, W * 0.76, blockH, 10);
        ctx.fill();
        ctx.restore();
        ctx.fillStyle = BLACK;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = '900 25px "Segoe UI", Inter, system-ui, sans-serif';
        nameLines3.forEach((line, i) => {
            ctx.fillText(line, W / 2, nameY0 + i * lineH);
        });

        // --- % sa lijeve i desne strane (preko template krugova) ---
        function drawPctBadge(cx, cy, r) {
            ctx.save();
            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
            const g = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.3, 4, cx, cy, r);
            g.addColorStop(0, GREEN_LIGHT);
            g.addColorStop(0.55, GREEN);
            g.addColorStop(1, GREEN_DARK);
            ctx.fillStyle = g;
            ctx.fill();
            ctx.lineWidth = 3;
            ctx.strokeStyle = 'rgba(255,255,255,0.55)';
            ctx.stroke();
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            const label = `−${pctLabel}%`;
            ctx.font = pctLabel.length > 2
                ? '900 28px Impact, "Arial Black", "Segoe UI", sans-serif'
                : '900 34px Impact, "Arial Black", "Segoe UI", sans-serif';
            ctx.fillText(label, cx, cy + 1);
            ctx.restore();
        }
        drawPctBadge(78, 520, 52);
        drawPctBadge(722, 500, 52);

        // --- Cijene na dnu ---
        // CIJENA natpis iznad; ispod: stara precrtana + pored veća crvena akcijska
        const priceBlockY = 700;
        const oldText = formatPrice(basePrice);
        const saleText = formatPrice(salePrice);

        // bijela podloga da se stari "CIJENA" iz templatea ne vidi
        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.94)';
        roundRect(ctx, W * 0.12, priceBlockY - 36, W * 0.76, 118, 14);
        ctx.fill();
        ctx.restore();

        // Natpis CIJENA (iznad brojeva)
        ctx.save();
        greenBrush(ctx, W / 2 - 110, priceBlockY - 28, 220, 36);
        ctx.fillStyle = '#ffffff';
        ctx.font = '900 22px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('CIJENA', W / 2, priceBlockY - 8);
        ctx.restore();

        // Red cijena: stara precrtana | akcijska veća i upečatljiva
        ctx.save();
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        const rowY = priceBlockY + 48;

        ctx.font = '700 26px "Segoe UI", Inter, system-ui, sans-serif';
        const oldW = ctx.measureText(oldText).width;
        ctx.font = '900 52px Impact, "Arial Black", "Segoe UI", sans-serif';
        const saleW = ctx.measureText(saleText).width;
        const gap = 22;
        const totalW = oldW + gap + saleW;
        let x = W / 2 - totalW / 2;

        // stara precrtana (manja, siva)
        ctx.font = '700 26px "Segoe UI", Inter, system-ui, sans-serif';
        ctx.fillStyle = '#64748b';
        ctx.fillText(oldText, x, rowY);
        ctx.strokeStyle = RED;
        ctx.lineWidth = 3.2;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(x - 4, rowY + 8);
        ctx.lineTo(x + oldW + 4, rowY - 10);
        ctx.stroke();
        x += oldW + gap;

        // akcijska — crvena, velika, bold, sa blagom sjenom
        ctx.font = '900 52px Impact, "Arial Black", "Segoe UI", sans-serif';
        ctx.shadowColor = 'rgba(225, 29, 72, 0.35)';
        ctx.shadowBlur = 10;
        ctx.shadowOffsetY = 2;
        ctx.fillStyle = RED;
        ctx.fillText(saleText, x, rowY);
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;

        // mali "AKCIJA" chip pored/ ispod da se odmah vidi
        const chipX = x + saleW / 2;
        const chipY = rowY + 36;
        ctx.font = '900 12px "Segoe UI", Inter, system-ui, sans-serif';
        const chipText = 'AKCIJSKA CIJENA';
        const chipW = ctx.measureText(chipText).width + 16;
        roundRect(ctx, chipX - chipW / 2, chipY - 10, chipW, 20, 10);
        ctx.fillStyle = RED;
        ctx.fill();
        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.fillText(chipText, chipX, chipY + 1);
        ctx.restore();
    }

    /**
     * Regularno / NOVO PONUDA — template bez %, naziv ispod headera,
     * veliki artikal u sredini, lijepa cijena ispod natpisa CIJENA.
     */
    async function drawRegularFromTemplate(canvas, { name, imageUrl, basePrice, templateUrl }) {
        const ctx = canvas.getContext('2d');
        const W = SIZE;
        const H = SIZE;

        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, W, H);

        const template = await loadImage(templateUrl);
        if (template) {
            ctx.drawImage(template, 0, 0, W, H);
        } else {
            drawFrameSplashes(ctx, W, H);
            ctx.fillStyle = GREEN;
            ctx.font = '900 56px Impact, "Arial Black", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('NOVO', W / 2, 80);
            ctx.fillStyle = BLACK;
            ctx.font = '900 28px "Segoe UI", sans-serif';
            ctx.fillText('PONUDA', W / 2, 120);
        }

        // --- Veliki artikal u sredini; naziv + cijena poslije frame overlay-a ---
        const nameUpper = String(name || 'ARTIKAL').toUpperCase();
        ctx.font = '900 26px "Segoe UI", Inter, system-ui, sans-serif';
        const nameLines = (() => {
            const words = String(nameUpper).split(/\s+/).filter(Boolean);
            const lines = [];
            let line = '';
            const maxW = W * 0.78;
            words.forEach((word) => {
                const test = line ? `${line} ${word}` : word;
                if (ctx.measureText(test).width > maxW && line) {
                    lines.push(line);
                    line = word;
                } else {
                    line = test;
                }
            });
            if (line) lines.push(line);
            return lines.slice(0, 3);
        })();

        const nameY0 = 194;
        const lineH = 30;
        const nameExtra = (nameLines.length - 1) * 12;
        const imgBox = {
            x: 70,
            y: 250 + nameExtra,
            w: 660,
            h: 370 - nameExtra,
        };
        const img = await loadImage(imageUrl);
        let drawnImgRect = { ...imgBox };
        if (img) {
            const scale = Math.min(imgBox.w / img.naturalWidth, imgBox.h / img.naturalHeight) * 0.98;
            const dw = img.naturalWidth * scale;
            const dh = img.naturalHeight * scale;
            const dx = imgBox.x + (imgBox.w - dw) / 2;
            const dy = imgBox.y + (imgBox.h - dh) / 2;
            ctx.drawImage(img, dx, dy, dw, dh);
            drawnImgRect = { x: dx, y: dy, w: dw, h: dh };
        } else {
            ctx.fillStyle = '#94a3b8';
            ctx.font = '600 18px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('Nema slike artikla', W / 2, imgBox.y + imgBox.h / 2);
        }

        // Mrlje / template rub PREKO bijele pozadine artikla
        compositeTemplateFrameOver(ctx, template, W, H, drawnImgRect);

        // Naziv ispod NOVO PONUDA
        const blockH = nameLines.length * lineH + 14;
        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.9)';
        roundRect(ctx, W * 0.1, nameY0 - 20, W * 0.8, blockH, 12);
        ctx.fill();
        ctx.strokeStyle = hexAlpha(GREEN, 0.45);
        ctx.lineWidth = 2;
        roundRect(ctx, W * 0.1, nameY0 - 20, W * 0.8, blockH, 12);
        ctx.stroke();
        ctx.restore();
        ctx.fillStyle = BLACK;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = '900 25px "Segoe UI", Inter, system-ui, sans-serif';
        nameLines.forEach((line, i) => {
            ctx.fillText(line, W / 2, nameY0 + i * lineH);
        });

        // --- Iznos ispod template natpisa CIJENA (čitljiva "kartica") ---
        const priceText = formatPrice(basePrice);
        // template "CIJENA" je oko y~700–720; broj ide odmah ispod
        const priceY = 758;

        ctx.save();
        ctx.font = '900 50px Impact, "Arial Black", "Segoe UI", sans-serif';
        const tw = ctx.measureText(priceText).width;
        const cardW = Math.min(W * 0.72, Math.max(200, tw + 48));
        const cardH = 64;
        const cardX = W / 2 - cardW / 2;
        const cardY = priceY - cardH / 2;

        ctx.shadowColor = 'rgba(15, 23, 42, 0.2)';
        ctx.shadowBlur = 12;
        ctx.shadowOffsetY = 3;
        roundRect(ctx, cardX, cardY, cardW, cardH, 14);
        ctx.fillStyle = '#ffffff';
        ctx.fill();
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.lineWidth = 3;
        ctx.strokeStyle = GREEN;
        roundRect(ctx, cardX, cardY, cardW, cardH, 14);
        ctx.stroke();

        ctx.fillStyle = GREEN_DEEP;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = '900 48px Impact, "Arial Black", "Segoe UI", sans-serif';
        ctx.fillText(priceText, W / 2, priceY + 1);
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
        const pctField = $('staffObjavaPctField');
        const pctReq = $('staffObjavaPctReq');
        const modeAkcija = $('staffObjavaModeAkcija');
        const modeRegular = $('staffObjavaModeRegular');
        const genBtn = $('staffObjavaGenerate');
        const downloadLink = $('staffObjavaDownload');
        const canvas = $('staffObjavaCanvas');
        const status = $('staffObjavaStatus');
        const activateWrap = $('staffObjavaActivateWrap');
        const activateBtn = $('staffObjavaActivate');
        const hoursInput = $('staffObjavaHours');
        const untilInput = $('staffObjavaUntil');

        const productName = btn.dataset.productName || 'Artikal';
        const productSlug = btn.dataset.productSlug || 'artikal';
        const basePrice = parsePrice(btn.dataset.productPrice);
        const imageUrl = btn.dataset.productImage || '';
        const akcijaTemplateUrl = btn.dataset.akcijaTemplate || '/static/img/objava-akcija-template-800.png';
        const regularTemplateUrl = btn.dataset.regularTemplate || '/static/img/objava-regular-template-800.png';
        const activateUrl = btn.dataset.activateUrl || '';
        let lastGeneratedPct = null;

        function currentMode() {
            if (modeRegular && modeRegular.checked) return 'regularno';
            return 'akcija';
        }

        function setActivateVisible(show) {
            if (activateWrap) activateWrap.hidden = !show;
        }

        function syncModeUi() {
            const mode = currentMode();
            const isAkcija = mode === 'akcija';
            if (pctField) pctField.hidden = !isAkcija;
            if (pctReq) {
                pctReq.textContent = isAkcija ? '(obavezno za akciju)' : '';
            }
            if (pctInput) {
                pctInput.placeholder = 'npr. 20';
                pctInput.required = isAkcija;
                if (!isAkcija) pctInput.value = '';
            }
            if (!isAkcija) {
                setActivateVisible(false);
                lastGeneratedPct = null;
            } else if (lastGeneratedPct != null) {
                setActivateVisible(true);
            } else {
                setActivateVisible(false);
            }
        }

        function open() {
            overlay.hidden = false;
            document.body.classList.add('popup-open');
            if (modeAkcija) modeAkcija.checked = true;
            if (pctInput) pctInput.value = '';
            if (hoursInput) hoursInput.value = '';
            if (untilInput) untilInput.value = '';
            if (downloadLink) downloadLink.hidden = true;
            lastGeneratedPct = null;
            setActivateVisible(false);
            if (status) {
                status.hidden = true;
                status.textContent = '';
            }
            syncModeUi();
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
            const mode = currentMode();
            const percent = parsePct();

            if (mode === 'akcija' && percent == null) {
                if (status) {
                    status.hidden = false;
                    status.textContent = 'Za Akciju unesi popust % (npr. 15 ili 20).';
                }
                setActivateVisible(false);
                lastGeneratedPct = null;
                pctInput && pctInput.focus();
                return;
            }

            if (status) {
                status.hidden = false;
                status.textContent = 'Generišem…';
            }
            if (genBtn) genBtn.disabled = true;
            try {
                if (mode === 'akcija') {
                    await drawAkcijaFromTemplate(canvas, {
                        name: productName,
                        imageUrl,
                        basePrice,
                        percent,
                        templateUrl: akcijaTemplateUrl,
                    });
                } else {
                    await drawRegularFromTemplate(canvas, {
                        name: productName,
                        imageUrl,
                        basePrice,
                        templateUrl: regularTemplateUrl,
                    });
                }
                const dataUrl = canvas.toDataURL('image/png');
                if (downloadLink) {
                    const safe = (productSlug || 'objava').replace(/[^\w\-]+/g, '_');
                    const pctPart = percent != null ? `-${percent}pct` : '';
                    const modePart = mode === 'akcija' ? 'akcija' : 'novo';
                    downloadLink.href = dataUrl;
                    downloadLink.download = `objava-${modePart}-${safe}${pctPart}-800x800.png`;
                    downloadLink.hidden = false;
                    downloadLink.textContent = 'Preuzmi PNG 800×800';
                }
                if (mode === 'akcija') {
                    lastGeneratedPct = percent;
                    setActivateVisible(true);
                    if (status) {
                        status.textContent = `Spremno — Akcija −${percent}%. Možeš preuzeti PNG ili aktivirati akciju na artiklu.`;
                    }
                } else {
                    lastGeneratedPct = null;
                    setActivateVisible(false);
                    if (status) {
                        status.textContent = 'Spremno — NOVO PONUDA (naziv, veliki artikal, cijena — bez %).';
                    }
                }
            } catch (err) {
                if (status) status.textContent = 'Greška pri generisanju. Pokušaj ponovo.';
                console.error(err);
            } finally {
                if (genBtn) genBtn.disabled = false;
            }
        }

        async function activateAkcija() {
            if (!activateUrl) {
                if (status) {
                    status.hidden = false;
                    status.textContent = 'Nedostaje URL za aktivaciju akcije.';
                }
                return;
            }
            const percent = parsePct() != null ? parsePct() : lastGeneratedPct;
            if (percent == null) {
                if (status) {
                    status.hidden = false;
                    status.textContent = 'Unesi popust % pa generiši preview.';
                }
                return;
            }
            const body = new URLSearchParams();
            body.set('action', 'activate_akcija');
            body.set('akcija_postotak', String(percent));
            body.set('csrfmiddlewaretoken', getCsrfToken());
            // Samo sati ILI samo datum (ne oba)
            const hoursRaw = ((hoursInput && hoursInput.value) || '').trim();
            const untilRaw = ((untilInput && untilInput.value) || '').trim();
            if (hoursRaw) {
                body.set('akcija_sati', hoursRaw);
            } else if (untilRaw) {
                body.set('akcija_do', untilRaw);
            }

            if (activateBtn) activateBtn.disabled = true;
            if (status) {
                status.hidden = false;
                status.textContent = 'Aktiviram akciju na artiklu…';
            }
            try {
                const res = await fetch(activateUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': getCsrfToken(),
                        'X-Requested-With': 'XMLHttpRequest',
                        Accept: 'application/json',
                    },
                    credentials: 'same-origin',
                    body: body.toString(),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.ok) {
                    throw new Error(data.message || 'Aktivacija akcije nije uspjela.');
                }
                if (status) status.textContent = data.message || 'Akcija aktivirana.';
                // osvježi cijene na stranici nakon kratke pauze
                window.setTimeout(() => {
                    window.location.reload();
                }, 900);
            } catch (err) {
                if (status) status.textContent = err.message || 'Greška pri aktivaciji.';
                console.error(err);
            } finally {
                if (activateBtn) activateBtn.disabled = false;
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
        activateBtn && activateBtn.addEventListener('click', activateAkcija);
        pctInput && pctInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                generate();
            }
        });
        modeAkcija && modeAkcija.addEventListener('change', syncModeUi);
        modeRegular && modeRegular.addEventListener('change', syncModeUi);
        // Sati ili datum — ne oba odjednom
        if (hoursInput && untilInput) {
            hoursInput.addEventListener('input', () => {
                if (hoursInput.value) untilInput.value = '';
            });
            untilInput.addEventListener('input', () => {
                if (untilInput.value) hoursInput.value = '';
            });
        }
        syncModeUi();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
