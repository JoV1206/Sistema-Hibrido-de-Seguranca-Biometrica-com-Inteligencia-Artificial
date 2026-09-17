let arquivoAtualParaIdentificar = null;
let todosOsLogsEmMemoria = []; 

// ==========================================
// 1. CARREGAMENTO E RENDERIZAÇÃO
// ==========================================

async function loadLogs() {
    const container = document.getElementById('logs-container');
    container.innerHTML = '<p style="grid-column: 1 / -1; text-align: center;">A carregar registos...</p>';

    try {
        todosOsLogsEmMemoria = await window.api.getLogs();
        applyFilters(); 
    } catch (error) {
        console.error("Erro ao carregar logs:", error);
    }
}

// Filtros Blindados contra Logs Antigos Corrompidos
function applyFilters() {
    const filterDate = document.getElementById('filter-date').value;
    const filterStatus = document.getElementById('filter-status').value;

    const logsFiltrados = todosOsLogsEmMemoria.filter(log => {
        // 1. Regra de Status
        if (filterStatus !== 'ALL' && log.status !== filterStatus) return false;

        // 2. Regra de Data (Converte de YYYY-MM-DD para DD/MM/YYYY)
        if (filterDate !== '') {
            const partes = filterDate.split('-'); 
            if (partes.length === 3) {
                const dataProcurada = `${partes[2]}/${partes[1]}/${partes[0]}`; 
                const dataLog = log.data_hora || '';
                if (!dataLog.startsWith(dataProcurada)) return false;
            }
        }

        return true; 
    });

    renderizarCartoes(logsFiltrados);
}

function renderizarCartoes(logs) {
    const container = document.getElementById('logs-container');
    
    if (logs.length === 0) {
        container.innerHTML = '<p style="grid-column: 1 / -1; text-align: center;">Nenhum registo encontrado para os filtros selecionados.</p>';
        document.getElementById('stat-success').innerText = '0';
        document.getElementById('stat-blocked').innerText = '0';
        document.getElementById('stat-total').innerText = '0';
        return;
    }

    let successCount = 0;
    let blockedCount = 0;
    container.innerHTML = '';

    logs.forEach(log => {
        const isSuccess = log.status === 'SUCESSO';
        if (isSuccess) successCount++; else blockedCount++;

        const badgeClass = isSuccess ? 'badge-success' : 'badge-danger';
        const imgTag = log.foto_base64 
            ? `<img src="${log.foto_base64}" alt="Captura da Câmara">` 
            : `<div style="height: 200px; display:flex; align-items:center; justify-content:center; background:#000; color:#fff;">Sem Câmara</div>`;

        const jsonFilename = log.foto_arquivo ? log.foto_arquivo.replace('.jpg', '.json') : '';
        const vetorTag = log.vetor_facial ? `<small style="color:var(--accent);">🧬 Vetor Biométrico Salvo</small>` : ``;

        const card = document.createElement('div');
        card.className = 'log-card';
        card.innerHTML = `
            ${imgTag}
            <div class="log-details">
                <p><strong>Status:</strong> <span class="${badgeClass}">${log.status}</span></p>
                <p><strong>Data:</strong> ${log.data_hora || 'Desconhecida'}</p>
                <p><strong>Usuário:</strong> <span style="color:var(--accent); font-weight:bold;">${log.usuario_identificado || 'Desconhecido'}</span></p>
                <p><strong>Motivo:</strong> ${log.motivo || 'N/A'}</p>
                ${vetorTag}

                <div style="margin-top: 15px; display: flex; gap: 5px;">
                    <button class="primary-btn btn-identify" data-file="${jsonFilename}" style="flex:1; padding: 5px; font-size:0.8rem;">Identificar</button>
                    <button class="danger-btn btn-delete" data-file="${jsonFilename}" style="flex:1; padding: 5px; font-size:0.8rem;">Excluir</button>
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    document.getElementById('stat-success').innerText = successCount;
    document.getElementById('stat-blocked').innerText = blockedCount;
    document.getElementById('stat-total').innerText = logs.length;
}

// ==========================================
// 2. EVENTOS DOS FILTROS E BOTÕES
// ==========================================

// Gatilhos mais suaves, blindados contra erros para não travar a digitação
document.getElementById('filter-date')?.addEventListener('input', () => {
    try { applyFilters(); } catch (e) { console.error(e); }
});
document.getElementById('filter-status')?.addEventListener('change', () => {
    try { applyFilters(); } catch (e) { console.error(e); }
});

document.getElementById('btn-clear-filters')?.addEventListener('click', () => {
    document.getElementById('filter-date').value = '';
    document.getElementById('filter-status').value = 'ALL';
    try { applyFilters(); } catch (e) { console.error(e); }
});

document.getElementById('btn-clear-logs')?.addEventListener('click', async () => {
    if (confirm("ATENÇÃO: Tem a certeza absoluta que deseja eliminar TODOS os registos e fotos de intrusão? Esta ação não pode ser desfeita.")) {
        const sucesso = await window.api.clearLogs();
        if (sucesso) {
            alert("Histórico apagado com sucesso!");
            loadLogs(); 
        } else {
            alert("Ocorreu um erro ao tentar limpar os ficheiros.");
        }
    }
});

document.addEventListener('click', async (event) => {
    if (event.target.classList.contains('btn-identify')) {
        arquivoAtualParaIdentificar = event.target.getAttribute('data-file');
        if (!arquivoAtualParaIdentificar) return alert("Erro: Ficheiro não encontrado.");
        
        document.getElementById('identify-name-input').value = '';
        document.getElementById('identify-modal').classList.add('active');
        document.getElementById('identify-name-input').focus();
    }
    
    if (event.target.classList.contains('btn-delete')) {
        const filename = event.target.getAttribute('data-file');
        if (!filename) return;
        
        if (confirm("Deseja deletar este registo permanentemente?")) {
            const sucesso = await window.api.deleteLog(filename);
            if (sucesso) loadLogs();
        }
    }
});

document.getElementById('btn-cancel-identify')?.addEventListener('click', () => {
    document.getElementById('identify-modal').classList.remove('active');
    arquivoAtualParaIdentificar = null;
});

document.getElementById('btn-confirm-identify')?.addEventListener('click', async () => {
    const newName = document.getElementById('identify-name-input').value.trim();
    if (newName && arquivoAtualParaIdentificar) {
        const sucesso = await window.api.identifyLog(arquivoAtualParaIdentificar, newName);
        if (sucesso) {
            document.getElementById('identify-modal').classList.remove('active');
            arquivoAtualParaIdentificar = null;
            loadLogs(); 
        }
    }
});

window.addEventListener('DOMContentLoaded', loadLogs);