(function () {
    var OUT = 800;

    function clamp(n, a, b) {
        return Math.max(a, Math.min(b, n));
    }

    function initRoot(root) {
        var stage = root.querySelector('[data-fit-stage]');
        var img = root.querySelector('[data-fit-img]');
        var range = root.querySelector('[data-fit-range]');
        var flag = root.querySelector('[data-fit-flag]');
        if (!stage || !img) return;
        var form = root.closest('form');
        var fileIds = (root.getAttribute('data-fit-files') || '').split(',').map(function (s) {
            return s.trim();
        }).filter(Boolean);
        var fileInputs = fileIds.map(function (id) {
            return document.getElementById(id);
        }).filter(Boolean);

        var zoom = 1;
        var tx = 0;
        var ty = 0;
        var base = 1;
        var loaded = false;
        var objectUrl = '';
        var pointers = {};
        var drag = null;
        var pinch = null;

        function stageSize() {
            return Math.max(1, stage.clientWidth || 280);
        }

        function apply() {
            if (!loaded) return;
            var s = base * zoom;
            img.style.transform = 'translate(-50%, -50%) translate(' + tx + 'px, ' + ty + 'px) scale(' + s + ')';
            if (range) range.value = String(Math.round(zoom * 100));
        }

        function resetView() {
            var nw = img.naturalWidth || 1;
            var nh = img.naturalHeight || 1;
            var S = stageSize();
            base = Math.min(S / nw, S / nh) * 0.92;
            zoom = 1;
            tx = 0;
            ty = 0;
            apply();
        }

        function setZoom(next) {
            zoom = clamp(next, 0.45, 3.2);
            apply();
        }

        function loadFile(file) {
            if (!file) {
                loaded = false;
                root.hidden = true;
                if (flag) flag.value = '0';
                return;
            }
            if (objectUrl) {
                try { URL.revokeObjectURL(objectUrl); } catch (e) {}
            }
            objectUrl = URL.createObjectURL(file);
            img.onload = function () {
                loaded = true;
                root.hidden = false;
                resetView();
            };
            img.src = objectUrl;
        }

        fileInputs.forEach(function (input, idx) {
            input.addEventListener('change', function () {
                var file = input.files && input.files[0];
                if (file) {
                    fileInputs.forEach(function (other, j) {
                        if (j !== idx) {
                            try { other.value = ''; } catch (e) {}
                        }
                    });
                }
                loadFile(file || null);
            });
        });

        if (range) {
            range.addEventListener('input', function () {
                setZoom((Number(range.value) || 100) / 100);
            });
        }
        root.querySelectorAll('[data-fit-zoom-out]').forEach(function (btn) {
            btn.addEventListener('click', function () { setZoom(zoom - 0.12); });
        });
        root.querySelectorAll('[data-fit-zoom-in]').forEach(function (btn) {
            btn.addEventListener('click', function () { setZoom(zoom + 0.12); });
        });
        root.querySelectorAll('[data-fit-reset]').forEach(function (btn) {
            btn.addEventListener('click', resetView);
        });

        stage.addEventListener('wheel', function (event) {
            if (!loaded) return;
            event.preventDefault();
            var dir = event.deltaY > 0 ? -0.08 : 0.08;
            setZoom(zoom + dir);
        }, { passive: false });

        function pointerList() {
            return Object.keys(pointers).map(function (id) { return pointers[id]; });
        }
        function pinchDist() {
            var pts = pointerList();
            if (pts.length < 2) return 0;
            var dx = pts[0].x - pts[1].x;
            var dy = pts[0].y - pts[1].y;
            return Math.sqrt(dx * dx + dy * dy) || 1;
        }

        stage.addEventListener('pointerdown', function (event) {
            if (!loaded) return;
            event.preventDefault();
            stage.setPointerCapture(event.pointerId);
            pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
            var pts = pointerList();
            if (pts.length === 1) {
                drag = { x: event.clientX, y: event.clientY, tx: tx, ty: ty };
                pinch = null;
                stage.classList.add('is-drag');
            } else if (pts.length >= 2) {
                drag = null;
                pinch = { dist: pinchDist(), zoom: zoom };
            }
        });
        stage.addEventListener('pointermove', function (event) {
            if (!pointers[event.pointerId]) return;
            pointers[event.pointerId] = { x: event.clientX, y: event.clientY };
            if (pinch && pointerList().length >= 2) {
                var d = pinchDist();
                setZoom(pinch.zoom * (d / pinch.dist));
                return;
            }
            if (drag) {
                tx = drag.tx + (event.clientX - drag.x);
                ty = drag.ty + (event.clientY - drag.y);
                apply();
            }
        });
        function endPointer(event) {
            delete pointers[event.pointerId];
            var n = pointerList().length;
            if (n < 2) pinch = null;
            if (n < 1) {
                drag = null;
                stage.classList.remove('is-drag');
            }
        }
        stage.addEventListener('pointerup', endPointer);
        stage.addEventListener('pointercancel', endPointer);

        function exportCanvas() {
            var canvas = document.createElement('canvas');
            canvas.width = OUT;
            canvas.height = OUT;
            var ctx = canvas.getContext('2d');
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, OUT, OUT);
            var S = stageSize();
            var k = OUT / S;
            var s = base * zoom;
            var w = img.naturalWidth * s * k;
            var h = img.naturalHeight * s * k;
            var x = OUT / 2 + tx * k - w / 2;
            var y = OUT / 2 + ty * k - h / 2;
            ctx.drawImage(img, x, y, w, h);
            return new Promise(function (resolve) {
                if (canvas.toBlob) {
                    canvas.toBlob(function (blob) {
                        resolve(blob || null);
                    }, 'image/jpeg', 0.92);
                } else {
                    resolve(null);
                }
            });
        }

        function assignFile(blob) {
            if (!blob || !fileInputs.length) return false;
            var target = fileInputs[0];
            try {
                var file = new File([blob], 'slika.jpg', { type: 'image/jpeg' });
                var dt = new DataTransfer();
                dt.items.add(file);
                target.files = dt.files;
                fileInputs.slice(1).forEach(function (other) {
                    try { other.value = ''; } catch (e) {}
                });
                return true;
            } catch (e) {
                return false;
            }
        }

        if (form) {
            form.addEventListener('submit', function (event) {
                if (root.dataset.fitReady === '1' || !loaded) return;
                event.preventDefault();
                event.stopImmediatePropagation();
                exportCanvas().then(function (blob) {
                    if (blob && assignFile(blob) && flag) flag.value = '1';
                    root.dataset.fitReady = '1';
                    if (typeof form.requestSubmit === 'function') form.requestSubmit();
                    else form.submit();
                }).catch(function () {
                    root.dataset.fitReady = '1';
                    form.submit();
                });
            }, true);
        }
    }

    function boot() {
        document.querySelectorAll('[data-image-fit]').forEach(initRoot);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
