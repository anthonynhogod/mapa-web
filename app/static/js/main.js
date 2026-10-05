/* ============================================================
   MODAL BASE
============================================================ */

function abrirModalWithHTML(html) {
    const overlay = document.querySelector('.modal-overlay');
    const slot = document.getElementById('modal-dynamic-slot');
    slot.innerHTML = html;
    overlay.classList.remove('oculto');
}

function abrirModalWithNodes(nodeBuilder) {
    const overlay = document.querySelector('.modal-overlay');
    const slot = document.getElementById('modal-dynamic-slot');
    slot.innerHTML = '';
    slot.appendChild(nodeBuilder());
    overlay.classList.remove('oculto');
}

function clearFlashesFromOverlay() {
    const overlay = document.querySelector('.modal-overlay');
    if (!overlay) return;
    overlay.querySelectorAll('.flash').forEach(el => el.remove());
}

function fecharModal() {
    const overlay = document.querySelector('.modal-overlay');
    overlay.classList.add('oculto');
    const slot = document.getElementById('modal-dynamic-slot');
    if (slot) slot.innerHTML = '';
    clearFlashesFromOverlay();
}

/* Ao carregar página, mostra modal se houver mensagens */
window.addEventListener("DOMContentLoaded", () => {
    const modal = document.querySelector(".modal-overlay");
    const hasMessages = modal.querySelectorAll(".flash").length > 0;
    if (hasMessages) modal.classList.remove("oculto");
});

/* ============================================================
   CSRF
============================================================ */

function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

/* ============================================================
   CONFIRMAR EXCLUSÃO
============================================================ */

function confirmarExclusao(btnEl) {
    const url = btnEl.dataset.url;
    const descricao = btnEl.dataset.descricao || 'este registro';

    abrirModalWithNodes(() => {
        const wrap = document.createElement('div');
        wrap.className = 'flash flash--warning';

        wrap.innerHTML = `
            <p>Tem certeza que deseja excluir <b>${descricao}</b>? Esta ação não pode ser desfeita.</p>
            <div style="display:flex; gap:.5rem; margin-top:.75rem;">
                <form action="${url}" method="post" style="display:flex; gap:.5rem;">
                    <input type="hidden" name="confirm" value="yes">

                    ${getCSRFToken() ? `
                    <input type="hidden" name="csrf_token" value="${getCSRFToken()}">` : ''}

                    <button type="submit" class="btn btn--danger">Sim, excluir</button>
                </form>

                <button type="button" class="btn btn--outline" onclick="fecharModal()">Cancelar</button>
            </div>
        `;

        return wrap;
    });
}

/* ============================================================
   DARK MODE
============================================================ */

document.addEventListener("DOMContentLoaded", () => {
    const root = document.documentElement;
    const btn = document.getElementById('theme-toggle');
    const txt = btn?.querySelector('.theme-toggle-text');
    const icon = btn?.querySelector('i');

    function currentTheme() {
        const explicit = root.getAttribute('data-theme');
        if (explicit) return explicit;

        return window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark'
            : 'light';
    }

    function setTheme(mode) {
        if (mode === 'dark' || mode === 'light') {
            root.setAttribute('data-theme', mode);
            localStorage.setItem('theme', mode);
        } else {
            root.removeAttribute('data-theme');
            localStorage.removeItem('theme');
        }
        paintButton();
    }

    function toggleTheme() {
        setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    }

    function paintButton() {
        if (!btn) return;

        const cur = currentTheme();
        txt.textContent = cur === 'dark' ? 'Light' : 'Dark';

        icon.classList.remove('fa-sun', 'fa-moon');
        icon.classList.add(cur === 'dark' ? 'fa-sun' : 'fa-moon');
    }

    if (btn) btn.addEventListener('click', toggleTheme);

    // Atualiza se sistema mudar
    if (!localStorage.getItem('theme') && window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)')
            .addEventListener('change', paintButton);
    }

    // Carrega tema salvo
    const saved = localStorage.getItem('theme');
    if (saved) {
        setTheme(saved);
    } else {
        paintButton();
    }
});

/* ============================================================
   LOADING GLOBAL
============================================================ */

(function () {
    const root = document.documentElement;
    let timer = null;
    let safetyTimer = null;
    const SAFETY_MS = 8000; // trava máxima: nunca fica preso indefinidamente

    // Estado próprio em JS, em vez de "perguntar ao DOM" via classList.
    // Isso é essencial: classList.add()/remove() reescreve o atributo
    // `class` (e por consequência dispara o MutationObserver abaixo)
    // TODA VEZ que o atributo já existe no elemento — mesmo quando a
    // chamada é um no-op (token que já não está presente). Como o
    // callback do observer pode chamar removeLoading() de novo, isso
    // criava um loop de mutações auto-alimentado (o observer reage à
    // própria mutação que ele mesmo causou, indefinidamente) em
    // qualquer página com um `.tab-pane.active` permanente no DOM,
    // como as telas de upload (GTA/DIF/SIF). Guardando com `isLoading`,
    // cada add/remove só toca o atributo quando é uma transição real.
    let isLoading = false;

    const addLoadingNow = () => {
        if (isLoading) return;
        isLoading = true;
        root.classList.add('loading');
        clearTimeout(safetyTimer);
        safetyTimer = setTimeout(removeLoading, SAFETY_MS);
    };

    const scheduleLoading = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
            if (isAnyModalOpen()) return;
            addLoadingNow();
        }, 120);
    };

    const removeLoading = () => {
        clearTimeout(timer);
        clearTimeout(safetyTimer);
        if (!isLoading) return;
        isLoading = false;
        root.classList.remove('loading');
    };

    function isAnyModalOpen() {
        return !!document.querySelector('.modal-overlay:not(.oculto)');
    }

    document.addEventListener(
        'submit',
        ev => {
            const form = ev.target;
            if (form && !form.matches('[data-no-loader="true"]')) scheduleLoading();
        },
        true
    );

    document.addEventListener(
        'click',
        ev => {
            const el = ev.target.closest('a, button, [role="button"]');
            if (!el) return;

            if (el.matches('[data-no-loader="true"]')) return;

            if (el.tagName === 'A') {
                const href = el.getAttribute('href') || '';

                if (href.startsWith('#')) return;
                if (el.target === '_blank') return;
                if (el.hasAttribute('download')) return;
                if (ev.ctrlKey || ev.shiftKey || ev.metaKey) return;
            }

            scheduleLoading();
        },
        true
    );

    // Tabs não podem ativar loader
    const mo = new MutationObserver(() => {
        if (document.querySelector('.tab-pane.active')) {
            removeLoading();
            return;
        }
        if (isAnyModalOpen()) removeLoading();
    });

    mo.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
    });

    document.addEventListener('DOMContentLoaded', removeLoading);
    window.addEventListener('load', removeLoading);
    window.addEventListener('pageshow', removeLoading);
})();

/* ============================================================
   FILE UPLOAD + CONFIRMAÇÃO DE OVERWRITE
============================================================ */

function attachConfirmBeforeOverwrite(formId, filetype) {
    const form = document.getElementById(formId);
    if (!form) return;

    form.addEventListener("submit", function (ev) {

        if (form.dataset.confirmed === "1") return;

        const hasTemp = form.dataset.hasTemp === "1";
        if (!hasTemp) return;

        const fileInput = form.querySelector('input[type="file"]');
        const fileObj = fileInput?.files?.[0];
        const selName = fileObj ? fileObj.name : null;
        if (!selName) return;

        ev.preventDefault();

        abrirModalWithNodes(() => {
            const wrap = document.createElement("div");
            wrap.className = "flash flash--warning";
            wrap.innerHTML = `
                <h3 style="margin-bottom:10px;">Substituir ${filetype.toUpperCase()} temporário?</h3>
                <p>Já existe um arquivo temporário vinculado a este registro.</p>

                <ul style="text-align:left;margin:10px 0;">
                    ${form.dataset.prev ? `<li>Atual: <code>${form.dataset.prev}</code></li>` : ''}
                    <li>Novo: <code>${selName}</code></li>
                </ul>

                <p>Continuar irá substituir o arquivo anterior.</p>

                <div style="margin-top:1rem; display:flex; gap:.5rem; justify-content:center;">
                    <button type="button" class="btn" id="btn-confirm-overwrite">Substituir</button>
                    <button type="button" class="btn btn-secondary" id="btn-cancel-overwrite">Cancelar</button>
                </div>
            `;

            wrap.querySelector("#btn-confirm-overwrite").addEventListener("click", () => {
                const hidden = document.createElement("input");
                hidden.type = "hidden";
                hidden.name = "confirm";
                hidden.value = "yes";
                form.appendChild(hidden);

                form.dataset.confirmed = "1";

                fecharModal();

                // Fix do bug submit
                HTMLFormElement.prototype.submit.call(form);
            });

            wrap.querySelector("#btn-cancel-overwrite").addEventListener("click", fecharModal);

            return wrap;
        });
    });
}

/* Inicializa lógica para GTA / DIF / SIF */
attachConfirmBeforeOverwrite("form-gta", "gta");
attachConfirmBeforeOverwrite("form-dif", "dif");
attachConfirmBeforeOverwrite("form-sif", "sif");