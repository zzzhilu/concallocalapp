/**
 * ConCall Local Model — Web UI Client (S94 Code Editor Pro)
 *
 * Features:
 * 1. Microphone recording (AudioWorklet) → float32 PCM
 * 2. WebSocket send audio chunks to app-gateway
 * 3. Receive and render real-time transcription, translation, speaker diarization, summary
 * 4. IDE-style tab navigation and terminal output
 */

// =============================================================================
// Constants
// =============================================================================
const WS_URL = `ws://${location.host}/ws`;
const SAMPLE_RATE = 16000;
const CHUNK_DURATION_MS = 250;
const CHUNK_SIZE = SAMPLE_RATE * (CHUNK_DURATION_MS / 1000);

// Speaker Colors (syntax-inspired)
const SPEAKER_COLORS = [
    { name: 'Speaker A', class: 's0', color: '#BD93F9' },  // purple
    { name: 'Speaker B', class: 's1', color: '#50FA7B' },  // green
    { name: 'Speaker C', class: 's2', color: '#F1FA8C' },  // yellow
    { name: 'Speaker D', class: 's3', color: '#8BE9FD' },  // cyan
    { name: 'Speaker E', class: 's4', color: '#FFB86C' },  // orange
    { name: 'Speaker F', class: 's5', color: '#FF79C6' },  // pink
];

// =============================================================================
// State
// =============================================================================
let ws = null;
let mediaStream = null;
let systemStream = null;
let audioContext = null;
let workletNode = null;
let sessionId = null;
let isRecording = false;
let isPaused = false;
let timerInterval = null;
let startTime = null;
let speakerMap = {};
let currentMode = 'zh';
let currentTab = 'transcription';
let lineCounter = 0;
let translationLineCounter = 0;

// Store all transcript segments (for diarization sync)
let transcriptSegments = [];

// =============================================================================
// DOM Elements
// =============================================================================
const statusIndicator = document.getElementById('statusIndicator');
const statusText = document.getElementById('statusText');
const statusBarConnection = document.getElementById('statusBarConnection');
const statusBarText = document.getElementById('statusBarText');
const timerEl = document.getElementById('timer');
const transcriptionPanel = document.getElementById('transcriptionPanel');
const translationPanel = document.getElementById('translationPanel');
const speakersPanel = document.getElementById('speakersPanel');
const speakersList = document.getElementById('speakersList');
const detectedLang = document.getElementById('detectedLang');
const levelBar = document.getElementById('levelBar');
const recordBtn = document.getElementById('recordBtn');
const stopBtn = document.getElementById('stopBtn');
const clearBtn = document.getElementById('clearBtn');
const exportBtn = document.getElementById('exportBtn');
const settingsBtn = document.getElementById('settingsBtn');
const settingsModal = document.getElementById('settingsModal');
const closeSettings = document.getElementById('closeSettings');
const summaryModal = document.getElementById('summaryModal');
const closeSummary = document.getElementById('closeSummary');
const summaryContent = document.getElementById('summaryContent');
const summaryModalContent = document.getElementById('summaryModalContent');
const copySummary = document.getElementById('copySummary');
const downloadSummary = document.getElementById('downloadSummary');
const audioDeviceSelect = document.getElementById('audioDeviceSelect');
const audioSourceSelect = document.getElementById('audioSourceSelect');
const modeZhBtn = document.getElementById('modeZh');
const modeEnBtn = document.getElementById('modeEn');
const modeToggleBtn = document.getElementById('modeToggleBtn');
const terminalBody = document.getElementById('terminalBody');
const terminalToggle = document.getElementById('terminalToggle');
const terminalPanel = document.getElementById('terminalPanel');
const breadcrumbFile = document.getElementById('breadcrumbFile');
const sidePanel = document.getElementById('sidePanel');
const explorerBtn = document.getElementById('explorerBtn');
const modeIndicator = document.getElementById('modeIndicator');
const summaryLoadingOverlay = document.getElementById('summaryLoadingOverlay');

// =============================================================================
// Tab Management
// =============================================================================
const tabs = document.querySelectorAll('.tab[data-tab]');
const panels = document.querySelectorAll('.editor-panel[data-tab]');
const treeItems = document.querySelectorAll('.tree-item[data-tab]');
const tabFilenames = {
    transcription: '逐字稿.ts',
    translation: '翻譯.ts',
    speakers: '說話者管理',
    summary: '摘要.md'
};

function switchTab(tabName) {
    currentTab = tabName;

    const panelsContainer = document.querySelector('.panels-container');
    const isSplitTab = (tabName === 'transcription' || tabName === 'translation');

    // Handle split-view interaction
    if (panelsContainer && currentMode === 'en-translate') {
        if (isSplitTab) {
            // Restore split-view for transcription/translation
            panelsContainer.classList.add('split-view');
            // Highlight both tabs
            tabs.forEach(t => {
                const tab = t.dataset.tab;
                t.classList.toggle('active', tab === 'transcription' || tab === 'translation');
            });
            // Both panels active
            panels.forEach(p => {
                const pt = p.dataset.tab;
                p.classList.toggle('active', pt === 'transcription' || pt === 'translation');
            });
            // Update tree items
            treeItems.forEach(t => {
                const tt = t.dataset.tab;
                t.classList.toggle('active', tt === 'transcription' || tt === 'translation');
            });
            if (breadcrumbFile) breadcrumbFile.textContent = '逐字稿.ts ↔ 翻譯.ts';
            return;
        } else {
            // Suspend split-view for speakers/summary
            panelsContainer.classList.remove('split-view');
        }
    }

    // Normal single-panel tab switch
    tabs.forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tabName);
    });

    panels.forEach(p => {
        p.classList.toggle('active', p.dataset.tab === tabName);
    });

    treeItems.forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tabName);
    });

    if (breadcrumbFile) {
        breadcrumbFile.textContent = tabFilenames[tabName] || tabName;
    }
}

// Bind tab clicks
tabs.forEach(tab => {
    tab.addEventListener('click', (e) => {
        if (e.target.closest('.tab-close')) return;
        switchTab(tab.dataset.tab);
    });
});

// Bind tree item clicks
treeItems.forEach(item => {
    item.addEventListener('click', () => switchTab(item.dataset.tab));
});

// =============================================================================
// Terminal
// =============================================================================
function addTerminalLine(text, type = '') {
    if (!terminalBody) return;
    const inputLine = terminalBody.querySelector('.terminal-input-line');
    const line = document.createElement('div');
    line.className = 'terminal-line';

    let className = 'terminal-text';
    if (type === 'success') className += ' terminal-success';
    else if (type === 'error') className += ' terminal-error';
    else if (type === 'warning') className += ' terminal-warning';
    else if (type === 'info') className += ' terminal-info';

    line.innerHTML = `<span class="${className}">${escapeHtml(text)}</span>`;

    if (inputLine) {
        terminalBody.insertBefore(line, inputLine);
    } else {
        terminalBody.appendChild(line);
    }
    terminalBody.scrollTop = terminalBody.scrollHeight;
}

if (terminalToggle) {
    terminalToggle.addEventListener('click', () => {
        terminalPanel.classList.toggle('collapsed');
    });
}

// =============================================================================
// Explorer Panel Toggle
// =============================================================================
if (explorerBtn) {
    explorerBtn.addEventListener('click', () => {
        explorerBtn.classList.toggle('active');
        const sideColumn = document.getElementById('sideColumn');
        if (sideColumn) sideColumn.classList.toggle('collapsed');
    });
}

// =============================================================================
// UI Renderers
// =============================================================================

function renderTranscripts() {
    transcriptionPanel.innerHTML = '';
    lineCounter = 0;

    if (transcriptSegments.length === 0) {
        transcriptionPanel.innerHTML = `
            <div class="empty-state" id="transcriptionEmpty">
                <div class="code-comment">
                    <span class="line-number">&nbsp;1</span>
                    <span class="comment-text">// ConCall AI — 即時逐字稿</span>
                </div>
                <div class="code-comment">
                    <span class="line-number">&nbsp;2</span>
                    <span class="comment-text">// 按下錄音按鈕 (●) 開始</span>
                </div>
                <div class="code-comment">
                    <span class="line-number">&nbsp;3</span>
                    <span class="text-muted">&nbsp;</span>
                </div>
                <div class="code-comment">
                    <span class="line-number">&nbsp;4</span>
                    <span class="keyword-text">export const</span>
                    <span class="variable-text"> status</span>
                    <span class="operator-text"> = </span>
                    <span class="string-text">"awaiting_input"</span>
                </div>
                <div class="cursor-line">
                    <span class="line-number">&nbsp;5</span>
                    <span class="cursor-blink">|</span>
                </div>
            </div>
        `;
        return;
    }

    let currentGroup = null;

    transcriptSegments.forEach(seg => {
        const speaker = seg.speaker || 'unknown';
        const speakerName = speakerMap[speaker]?.name || (speaker === 'unknown' ? '未知' : speaker);
        const speakerColor = speakerMap[speaker]?.color || '#858585';

        if (!currentGroup || currentGroup.speaker !== speaker) {
            currentGroup = {
                speaker: speaker,
                div: document.createElement('div')
            };
            currentGroup.div.className = 'transcript-group';
            currentGroup.div.style.borderLeftColor = speakerColor;

            // Speaker header line (comment style)
            lineCounter++;
            const header = document.createElement('div');
            header.className = 'group-header';
            header.innerHTML = `
                <span class="line-number">${padLineNum(lineCounter)}</span>
                <span class="comment-text">// </span>
                <span class="speaker-name" style="color:${speakerColor}">${escapeHtml(speakerName)}</span>
                <span class="group-time">&nbsp;— ${formatTimestamp(seg.timestamp)}</span>
            `;
            currentGroup.div.appendChild(header);

            currentGroup.contentDiv = document.createElement('div');
            currentGroup.contentDiv.className = 'group-content';
            currentGroup.div.appendChild(currentGroup.contentDiv);

            transcriptionPanel.appendChild(currentGroup.div);
        }

        // Content line
        lineCounter++;
        const p = document.createElement('div');
        p.className = 'transcript-line';
        p.innerHTML = `
            <span class="line-number">${padLineNum(lineCounter)}</span>
            <span class="line-time">${formatDuration(seg.start * 1000)}</span>
            <span class="line-text">${escapeHtml(seg.text)}</span>
        `;
        currentGroup.contentDiv.appendChild(p);
    });

    // Active cursor at end
    lineCounter++;
    const cursorLine = document.createElement('div');
    cursorLine.className = 'cursor-line';
    cursorLine.innerHTML = `
        <span class="line-number">${padLineNum(lineCounter)}</span>
        <span class="cursor-blink">|</span>
    `;
    transcriptionPanel.appendChild(cursorLine);

    // Auto scroll
    transcriptionPanel.scrollTop = transcriptionPanel.scrollHeight;
}

function addTranscription(data) {
    if (!data.segments || data.segments.length === 0) return;

    if (data.language) {
        detectedLang.textContent = data.language.toUpperCase();
    }

    data.segments.forEach(seg => {
        transcriptSegments.push({
            start: seg.start,
            end: seg.end,
            text: seg.text,
            speaker: null,
            timestamp: data.timestamp
        });
    });

    renderTranscripts();
    addTerminalLine(`[ASR] +${data.segments.length} 個片段已接收`, 'info');
}

function addTranslation(data) {
    const empty = document.getElementById('translationEmpty');
    if (empty) empty.style.display = 'none';

    translationLineCounter++;
    const entry = document.createElement('div');
    entry.className = 'translation-entry';
    entry.innerHTML = `
        <div class="entry-line">
            <span class="line-number">${padLineNum(translationLineCounter)}</span>
            <span class="comment-text">// ${escapeHtml(data.source_lang || '?')} → ${escapeHtml(data.target_lang || '?')}</span>
        </div>
        <div class="entry-original">${escapeHtml(data.original_text)}</div>
        <div class="entry-translated">${escapeHtml(data.translated_text)}</div>
    `;
    translationPanel.appendChild(entry);
    translationPanel.scrollTop = translationPanel.scrollHeight;

    addTerminalLine(`[翻譯] ${data.source_lang || '?'} → ${data.target_lang || '?'}`, 'info');
}

function updateSpeakers(data) {
    const speakers = data.speakers || [];
    if (speakers.length === 0) return;

    // 1. Update Speaker Map
    const uniqueSpeakers = [...new Set(speakers.map(s => s.speaker))];
    uniqueSpeakers.forEach((spk) => {
        if (!speakerMap[spk]) {
            const idx = Object.keys(speakerMap).length;
            const colorInfo = SPEAKER_COLORS[idx % SPEAKER_COLORS.length];
            speakerMap[spk] = { ...colorInfo };
        }
    });

    // 2. Count turns per speaker for stats
    const turnCounts = {};
    speakers.forEach(s => {
        turnCounts[s.speaker] = (turnCounts[s.speaker] || 0) + 1;
    });

    // 3. Update Speaker list in explorer tree
    updateSpeakerTreeList();

    // 4. Render speaker editor panel
    renderSpeakerEditor(turnCounts);

    // 5. Back-fill transcript segments with speaker info
    let updated = false;
    speakers.forEach(spkTurn => {
        transcriptSegments.forEach(seg => {
            if (seg.start < spkTurn.end - 0.5 && seg.end > spkTurn.start + 0.5) {
                if (!seg.speaker || seg.speaker === 'unknown') {
                    seg.speaker = spkTurn.speaker;
                    updated = true;
                }
            }
        });
    });

    if (updated) {
        renderTranscripts();
    }

    addTerminalLine(`[說話者辨識] 偵測到 ${uniqueSpeakers.length} 位說話者`, 'success');
}

function updateSpeakerTreeList() {
    speakersList.innerHTML = '';
    Object.entries(speakerMap).forEach(([label, info]) => {
        const item = document.createElement('div');
        item.className = 'speaker-tree-item';
        item.innerHTML = `
            <span class="speaker-dot" style="background:${info.color}"></span>
            <span class="speaker-label" style="color:${info.color}">${escapeHtml(info.name)}</span>
        `;
        speakersList.appendChild(item);
    });
}

function renderSpeakerEditor(turnCounts) {
    const editorList = document.getElementById('speakerEditorList');
    if (!editorList) return;

    const emptyEl = document.getElementById('speakerEditorEmpty');
    const entries = Object.entries(speakerMap);

    if (entries.length === 0) {
        if (emptyEl) emptyEl.style.display = 'flex';
        return;
    }

    if (emptyEl) emptyEl.style.display = 'none';

    // Remove old cards but keep empty state element
    const existingCards = editorList.querySelectorAll('.speaker-card');
    existingCards.forEach(c => c.remove());

    entries.forEach(([id, info]) => {
        const card = document.createElement('div');
        card.className = 'speaker-card';
        card.style.borderLeftColor = info.color;
        card.dataset.speakerId = id;

        const turns = (turnCounts && turnCounts[id]) || 0;

        card.innerHTML = `
            <div class="speaker-card__dot" style="background:${info.color}"></div>
            <div class="speaker-card__info">
                <input type="text"
                       class="speaker-card__input"
                       value="${escapeHtml(info.name)}"
                       data-speaker-id="${escapeHtml(id)}"
                       placeholder="輸入名稱..."
                       spellcheck="false"
                       style="color:${info.color}"
                />
                <div class="speaker-card__stats">
                    <span class="speaker-card__stat">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
                        </svg>
                        ${turns} 次發言
                    </span>
                </div>
            </div>
        `;

        // Attach input event listener
        const input = card.querySelector('.speaker-card__input');
        input.addEventListener('input', (e) => {
            onSpeakerNameChange(id, e.target.value);
        });

        editorList.appendChild(card);
    });
}

function onSpeakerNameChange(speakerId, newName) {
    if (!speakerMap[speakerId]) return;

    // Update the name in the map
    speakerMap[speakerId].name = newName || speakerMap[speakerId].name;

    // Refresh the side panel tree list
    updateSpeakerTreeList();

    // Re-render transcripts to update speaker prefixes
    renderTranscripts();
}

// =============================================================================
// JSON Syntax Highlighting (for speakers panel)
// =============================================================================
function syntaxHighlightJSON(str) {
    return str
        .replace(/(\".*?\")\s*:/g, '<span class="variable-text">$1</span>:')
        .replace(/:\s*(\".*?\")/g, ': <span class="string-text">$1</span>')
        .replace(/:\s*(\d+\.?\d*)/g, ': <span class="number-text">$1</span>')
        .replace(/:\s*(true|false|null)/g, ': <span class="keyword-text">$1</span>')
        .replace(/([{}\[\]])/g, '<span class="punctuation-text">$1</span>')
        .replace(/,$/g, '<span class="punctuation-text">,</span>');
}

// Accumulated streaming summary text
let _summaryBuffer = '';
let _summaryRawText = '';   // Store raw markdown for editing
let _summaryEditMode = false;
let _currentMeetingId = null; // Track which meeting is loaded

function showSummary(data) {
    const summaryText = data.summary || '無摘要內容';
    renderSummaryText(summaryText);
    addTerminalLine('[摘要] 會議摘要已生成', 'success');
}

function appendSummaryChunk(chunk) {
    _summaryBuffer += chunk;
    renderSummaryText(_summaryBuffer);
}

function renderSummaryText(text) {
    _summaryRawText = text;
    const html = simpleMarkdown(text);
    if (summaryContent) {
        summaryContent.innerHTML = `<div class="summary-content">${html}</div>`;
    }
    if (summaryModalContent) {
        summaryModalContent.innerHTML = `<div class="summary-content">${html}</div>`;
    }
    // Also update textarea if it exists
    const textarea = document.getElementById('summaryTextarea');
    if (textarea && !_summaryEditMode) {
        textarea.value = text;
    }
}

// ── Summary Edit / Preview / Highlight / Save ──

function switchSummaryMode(editMode) {
    _summaryEditMode = editMode;
    const textarea = document.getElementById('summaryTextarea');
    const previewBtn = document.getElementById('summaryPreviewBtn');
    const editBtn = document.getElementById('summaryEditBtn');

    if (editMode) {
        // Switch to edit mode
        if (textarea) {
            textarea.value = _summaryRawText;
            textarea.style.display = 'block';
        }
        if (summaryContent) summaryContent.style.display = 'none';
        if (previewBtn) previewBtn.classList.remove('active');
        if (editBtn) editBtn.classList.add('active');
    } else {
        // Switch to preview mode — apply edits from textarea
        if (textarea) {
            if (textarea.value.trim()) {
                _summaryRawText = textarea.value;
                const html = simpleMarkdown(_summaryRawText);
                if (summaryContent) {
                    summaryContent.innerHTML = `<div class="summary-content">${html}</div>`;
                }
                if (summaryModalContent) {
                    summaryModalContent.innerHTML = `<div class="summary-content">${html}</div>`;
                }
            }
            textarea.style.display = 'none';
        }
        if (summaryContent) summaryContent.style.display = '';
        if (previewBtn) previewBtn.classList.add('active');
        if (editBtn) editBtn.classList.remove('active');
    }
}

function highlightSelection() {
    const selection = window.getSelection();
    if (!selection.rangeCount || selection.isCollapsed) {
        addTerminalLine('[摘要] 請先選取要標記的文字', 'warning');
        return;
    }

    const range = selection.getRangeAt(0);

    // Check if selection is within summary content
    const container = summaryContent || document.getElementById('summaryContent');
    if (!container || !container.contains(range.commonAncestorContainer)) {
        addTerminalLine('[摘要] 請在摘要內容中選取文字', 'warning');
        return;
    }

    const mark = document.createElement('mark');
    mark.className = 'summary-highlight';
    try {
        range.surroundContents(mark);
    } catch (e) {
        // If selection spans multiple elements, wrap inline
        const fragment = range.extractContents();
        mark.appendChild(fragment);
        range.insertNode(mark);
    }
    selection.removeAllRanges();
    addTerminalLine('[摘要] 已標記選取文字', 'success');
}

async function saveSummaryEdits() {
    // Get the current summary text (from textarea if editing, or from rendered content)
    let summaryText = _summaryRawText;
    const textarea = document.getElementById('summaryTextarea');
    if (_summaryEditMode && textarea) {
        summaryText = textarea.value;
        _summaryRawText = summaryText;
        // Re-render preview
        switchSummaryMode(false);
    }

    // If we have a loaded meeting, update it
    if (_currentMeetingId) {
        try {
            // We need to get existing meeting data first, then update
            const res = await fetch(`/api/meetings/${_currentMeetingId}`);
            if (!res.ok) throw new Error('Meeting not found');
            const meeting = await res.json();

            // Delete old and save new with updated summary + auto-title
            await fetch(`/api/meetings/${_currentMeetingId}`, { method: 'DELETE' });

            const autoTitle = extractTitleFromSummary(summaryText);
            const body = {
                title: autoTitle || meeting.title,
                duration: meeting.duration,
                mode: meeting.mode,
                transcripts: meeting.transcripts,
                translations: meeting.translations,
                summary: summaryText,
                speakers: meeting.speakers
            };

            const saveRes = await fetch('/api/meetings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await saveRes.json();
            _currentMeetingId = data.id;
            addTerminalLine(`[儲存] 摘要已更新: ${data.title}`, 'success');
            loadMeetings();
        } catch (err) {
            addTerminalLine(`[儲存] 更新失敗: ${err.message}`, 'error');
        }
    } else {
        addTerminalLine('[儲存] 摘要已暫存（錄音結束後將自動儲存）', 'info');
    }

    // Flash save button 
    const saveBtn = document.getElementById('summarySaveBtn');
    if (saveBtn) {
        saveBtn.classList.add('saved');
        saveBtn.querySelector('span').textContent = '已儲存';
        setTimeout(() => {
            saveBtn.classList.remove('saved');
            saveBtn.querySelector('span').textContent = '儲存';
        }, 2000);
    }
}

// =============================================================================
// Utilities
// =============================================================================
function setStatus(className, text) {
    // Title bar status
    if (statusIndicator) {
        statusIndicator.className = `titlebar__status ${className}`;
    }
    if (statusText) statusText.textContent = text;

    // Status bar connection
    if (statusBarConnection) {
        statusBarConnection.className = `status-item status-sync ${className}`;
    }
    if (statusBarText) {
        statusBarText.textContent = text;
    }
}

function updateAudioLevel(rms) {
    const pct = Math.min(100, rms * 500);
    levelBar.style.width = pct + '%';
}

function startTimer() {
    startTime = Date.now();
    timerInterval = setInterval(() => {
        const diff = Date.now() - startTime;
        timerEl.textContent = formatDuration(diff);
    }, 1000);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function formatDuration(ms) {
    const s = Math.floor(ms / 1000) % 60;
    const m = Math.floor(ms / 60000) % 60;
    const h = Math.floor(ms / 3600000);
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function formatTimestamp(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function pad(n) {
    return String(n).padStart(2, '0');
}

function padLineNum(n) {
    return String(n).padStart(3, '\u00a0'); // nbsp padding
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function simpleMarkdown(md) {
    return md
        .replace(/^### (.*$)/gm, '<h3>$1</h3>')
        .replace(/^## (.*$)/gm, '<h2>$1</h2>')
        .replace(/^# (.*$)/gm, '<h1>$1</h1>')
        .replace(/^\- \[ \] (.*$)/gm, '<li>☐ $1</li>')
        .replace(/^\- \[x\] (.*$)/gm, '<li>☑ $1</li>')
        .replace(/^\- (.*$)/gm, '<li>$1</li>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}

function clearAll() {
    transcriptSegments = [];
    lineCounter = 0;
    translationLineCounter = 0;
    renderTranscripts();

    // Clear translation panel
    translationPanel.innerHTML = `
        <div class="empty-state" id="translationEmpty">
            <div class="code-comment">
                <span class="line-number">&nbsp;1</span>
                <span class="comment-text">// 翻譯輸出</span>
            </div>
            <div class="code-comment">
                <span class="line-number">&nbsp;2</span>
                <span class="comment-text">// 切換至英文模式以啟用翻譯</span>
            </div>
            <div class="cursor-line">
                <span class="line-number">&nbsp;3</span>
                <span class="cursor-blink">|</span>
            </div>
        </div>
    `;

    // Clear speakers
    speakersList.innerHTML = `
        <div class="tree-item speaker-empty">
            <span class="tree-filename comment-text">// 等待說話者辨識中...</span>
        </div>
    `;
    if (speakersPanel) {
        // Only reset the editor list contents, preserve DOM structure
        const editorList = document.getElementById('speakerEditorList');
        const editorEmpty = document.getElementById('speakerEditorEmpty');
        if (editorList) {
            // Remove speaker cards but keep empty placeholder
            editorList.querySelectorAll('.speaker-card').forEach(c => c.remove());
        }
        if (editorEmpty) {
            editorEmpty.style.display = 'flex';
        }
    }

    speakerMap = {};
    detectedLang.textContent = '—';
    timerEl.textContent = '00:00:00';

    addTerminalLine('[系統] 工作區已清除', 'warning');
}

function exportMarkdown() {
    let md = `# 會議記錄\n\n**日期：** ${new Date().toLocaleDateString('zh-TW')}\n\n---\n\n`;

    transcriptSegments.forEach(seg => {
        const speaker = speakerMap[seg.speaker]?.name || '未知';
        const time = formatDuration(seg.start * 1000);
        md += `**[${time}] ${speaker}:** ${seg.text}\n\n`;
    });

    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `會議記錄-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);

    addTerminalLine('[匯出] 會議記錄已匯出為 Markdown', 'success');
}

function downloadSummaryMd() {
    const content = summaryModalContent?.querySelector('.summary-content')
        || summaryContent?.querySelector('.summary-content');
    if (!content) return;
    const text = content.innerText;
    const blob = new Blob([text], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `摘要-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
}

function copySummaryText() {
    const content = summaryModalContent?.querySelector('.summary-content')
        || summaryContent?.querySelector('.summary-content');
    if (!content) return;
    navigator.clipboard.writeText(content.innerText).then(() => {
        copySummary.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="20 6 9 17 4 12"/>
            </svg>
            已複製！
        `;
        setTimeout(() => {
            copySummary.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                    <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                </svg>
                複製
            `;
        }, 2000);
    });
}

// =============================================================================
// Audio Device
// =============================================================================
async function loadAudioDevices() {
    try {
        await navigator.mediaDevices.getUserMedia({ audio: true });
        const devices = await navigator.mediaDevices.enumerateDevices();
        const audioInputs = devices.filter(d => d.kind === 'audioinput');

        audioDeviceSelect.innerHTML = audioInputs.map(d =>
            `<option value="${d.deviceId}">${d.label || 'Mic ' + d.deviceId.slice(0, 8)}</option>`
        ).join('');
    } catch (e) {
        console.warn('Cannot enumerate audio devices:', e);
        audioDeviceSelect.innerHTML = '<option value="">預設裝置</option>';
    }
}

// =============================================================================
// Event Bindings
// =============================================================================
recordBtn.addEventListener('click', () => {
    if (isRecording) {
        // Pause (future)
    } else {
        startRecording();
    }
});

stopBtn.addEventListener('click', stopRecording);
clearBtn.addEventListener('click', clearAll);
exportBtn.addEventListener('click', exportMarkdown);

settingsBtn.addEventListener('click', () => {
    loadAudioDevices();
    settingsModal.classList.add('active');
});
closeSettings.addEventListener('click', () => settingsModal.classList.remove('active'));
closeSummary.addEventListener('click', () => summaryModal.classList.remove('active'));
copySummary.addEventListener('click', copySummaryText);
downloadSummary.addEventListener('click', downloadSummaryMd);

// Click overlay to close modals
[summaryModal, settingsModal].forEach(modal => {
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('active');
    });
});

// --- Mode Selector (status bar) ---
function setMode(mode) {
    currentMode = mode;

    // Update status bar mode indicator
    if (modeZhBtn) modeZhBtn.classList.toggle('active', mode === 'zh');
    if (modeEnBtn) modeEnBtn.classList.toggle('active', mode === 'en-translate');

    // Update mode toggle button tooltip
    if (modeToggleBtn) {
        modeToggleBtn.setAttribute('data-tooltip',
            mode === 'zh' ? '模式：中文' : '模式：英文翻譯');
    }

    // Update status bar indicator
    if (modeIndicator) {
        modeIndicator.textContent = mode === 'zh' ? '中文' : 'EN 翻譯';
    }

    // Toggle split-view for translation mode
    const panelsContainer = document.querySelector('.panels-container');
    if (panelsContainer) {
        if (mode === 'en-translate') {
            panelsContainer.classList.add('split-view');
            // Highlight both transcription and translation tabs
            tabs.forEach(t => {
                const tab = t.dataset.tab;
                t.classList.toggle('active', tab === 'transcription' || tab === 'translation');
            });
            // Update breadcrumb
            if (breadcrumbFile) breadcrumbFile.textContent = '逐字稿.ts ↔ 翻譯.ts';
        } else {
            panelsContainer.classList.remove('split-view');
            // Restore single-tab view
            switchTab(currentTab === 'translation' ? 'transcription' : currentTab);
        }
    }

    addTerminalLine(`[設定] 語言模式已設為：${mode === 'zh' ? '中文' : '英文 (翻譯)'}`, 'info');
}

if (modeZhBtn) {
    modeZhBtn.addEventListener('click', () => setMode('zh'));
}
if (modeEnBtn) {
    modeEnBtn.addEventListener('click', () => setMode('en-translate'));
}

// Initialize default mode
setMode('zh');

// =============================================================================
// WebSocket & Audio Logic
// =============================================================================

function connectWebSocket() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log('[ConCall] WebSocket connected');
        setStatus('connected', '已連線');
        recordBtn.disabled = false;
        stopBtn.disabled = true;
        addTerminalLine('✓ WebSocket 連線已建立', 'success');
    };

    ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        const { event: evtType, data } = payload;

        switch (evtType) {
            case 'connected':
                sessionId = data.session_id;
                console.log('Session ID:', sessionId);
                addTerminalLine(`Session ID: ${sessionId}`, 'info');
                break;
            case 'status':
                console.log('Server status:', data.message);
                addTerminalLine(`[Status] ${data.message}`, 'info');
                break;
            case 'transcription':
                addTranscription(data);
                break;
            case 'translation':
                addTranslation(data);
                break;
            case 'diarization':
                updateSpeakers(data);
                break;
            case 'summary':
                if (data.type === 'summary_chunk') {
                    // Streaming: append chunk incrementally
                    if (summaryLoadingOverlay) summaryLoadingOverlay.classList.remove('active');
                    switchTab('summary');
                    appendSummaryChunk(data.chunk);
                } else if (data.type === 'summary_done') {
                    // Streaming complete
                    if (summaryLoadingOverlay) summaryLoadingOverlay.classList.remove('active');
                    _summaryBuffer = '';
                    showSummary(data);
                    if (data.summary) {
                        summaryModal.classList.add('active');
                        switchTab('summary');
                    }
                } else {
                    // Legacy non-streaming fallback
                    if (summaryLoadingOverlay) summaryLoadingOverlay.classList.remove('active');
                    showSummary(data);
                    if (data.summary) {
                        summaryModal.classList.add('active');
                        switchTab('summary');
                    }
                }
                break;
        }
    };

    ws.onclose = () => {
        console.log('[ConCall] WebSocket disconnected');
        setStatus('disconnected', '已斷線');
        recordBtn.disabled = true;
        stopBtn.disabled = true;
        addTerminalLine('✗ WebSocket 已斷線。 3 秒後重新連線...', 'error');
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (error) => {
        console.error('[ConCall] WebSocket error:', error);
        addTerminalLine('[錯誤] WebSocket 連線錯誤', 'error');
    };
}

async function startRecording() {
    if (isRecording) return;
    _summaryBuffer = ''; // reset streaming summary buffer

    try {
        const audioSource = audioSourceSelect ? audioSourceSelect.value : 'microphone';
        const deviceId = audioDeviceSelect.value;

        let sourceLabel = audioSource === 'microphone' ? '麥克風' : audioSource === 'system' ? '喇叭 (系統全域)' : '麥克風 + 喇叭';
        addTerminalLine(`[錄音] 啟動中... 來源=${sourceLabel}`, 'info');

        // --- 1. Acquire audio streams ---
        let micStream = null;
        let sysStream = null;

        // Helper: get system audio (always shows ONE dialog)
        const acquireSystemAudio = async () => {
            addTerminalLine('[喇叭] 請在彈出視窗中選擇「整個畫面」並勾選「分享系統音訊」', 'info');
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    displaySurface: 'monitor',
                    width: 1,
                    height: 1,
                    frameRate: 1
                },
                audio: {
                    channelCount: 1,
                    sampleRate: SAMPLE_RATE,
                    echoCancellation: false,
                    noiseSuppression: false,
                    autoGainControl: false
                },
                preferCurrentTab: false,
                systemAudio: 'include'
            });
            // Stop video track immediately — we only need audio
            stream.getVideoTracks().forEach(t => t.stop());
            if (stream.getAudioTracks().length === 0) {
                throw new Error('未偵測到音訊軌道。請確認已勾選「分享系統音訊」。');
            }
            addTerminalLine('[喇叭] 系統全域音訊已連接', 'success');
            return stream;
        };

        // Helper: get mic (silently — no dialog if already permitted)
        const acquireMic = async () => {
            const micConstraints = {
                audio: {
                    deviceId: deviceId ? { exact: deviceId } : undefined,
                    channelCount: 1,
                    sampleRate: SAMPLE_RATE,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            };
            return await navigator.mediaDevices.getUserMedia(micConstraints);
        };

        if (audioSource === 'both') {
            // ── "Both" mode: show ONLY the system audio dialog ──
            // 1) System audio first (this is the one visible dialog)
            try {
                sysStream = await acquireSystemAudio();
            } catch (displayErr) {
                if (displayErr.name === 'NotAllowedError') {
                    addTerminalLine('[喇叭] 使用者已取消', 'warning');
                    return;
                }
                throw displayErr;
            }
            // 2) Mic silently (no dialog if browser already has permission)
            try {
                micStream = await acquireMic();
                addTerminalLine('[麥克風] 已靜默連接', 'success');
            } catch (micErr) {
                console.warn('[麥克風] 無法靜默取得，僅使用系統音訊:', micErr.message);
                addTerminalLine('[麥克風] 無法取得，僅使用系統音訊', 'warning');
            }
        } else if (audioSource === 'microphone') {
            micStream = await acquireMic();
        } else if (audioSource === 'system') {
            try {
                sysStream = await acquireSystemAudio();
            } catch (displayErr) {
                if (displayErr.name === 'NotAllowedError') {
                    addTerminalLine('[喇叭] 使用者已取消', 'warning');
                    return;
                }
                throw displayErr;
            }
        }

        // --- 2. AudioContext + Worklet ---
        audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
        await audioContext.audioWorklet.addModule('/static/processor.js');
        workletNode = new AudioWorkletNode(audioContext, 'audio-processor');

        // --- 3. Connect sources ---
        if (micStream && sysStream) {
            const micSource = audioContext.createMediaStreamSource(micStream);
            const sysSource = audioContext.createMediaStreamSource(sysStream);
            const merger = audioContext.createChannelMerger(1);
            const micGain = audioContext.createGain();
            const sysGain = audioContext.createGain();
            micGain.gain.value = 1.0;
            sysGain.gain.value = 1.0;
            micSource.connect(micGain).connect(merger, 0, 0);
            sysSource.connect(sysGain).connect(merger, 0, 0);
            merger.connect(workletNode);
            mediaStream = micStream;
            systemStream = sysStream;
        } else {
            const activeStream = micStream || sysStream;
            const source = audioContext.createMediaStreamSource(activeStream);
            source.connect(workletNode);
            mediaStream = activeStream;
            systemStream = null;
        }

        // --- 4. Handle audio data ---
        workletNode.port.onmessage = (event) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(event.data);
            }
            const buffer = event.data;
            let sum = 0;
            for (let i = 0; i < buffer.length; i += 10) {
                sum += buffer[i] * buffer[i];
            }
            updateAudioLevel(Math.sqrt(sum / (buffer.length / 10)));
        };

        // --- 5. Signal server & update UI ---
        const startCmd = {
            action: 'start',
            language: currentMode,
            session_id: sessionId
        };
        ws.send(JSON.stringify(startCmd));

        isRecording = true;
        sourceLabel = audioSource === 'both' ? '混合' : (audioSource === 'system' ? '系統' : '麥克風');

        recordBtn.classList.add('recording-active');
        setRecordBtnIcon(ICON_PAUSE);
        recordBtn.setAttribute('data-tooltip', `暫停錄音 (${sourceLabel})`);
        stopBtn.disabled = false;
        setStatus('recording', `錄音中 (${sourceLabel})`);

        startTimer();
        clearAll();

        addTerminalLine(`✓ 錄音已開始 — ${sourceLabel} @ ${SAMPLE_RATE}Hz`, 'success');
        switchTab('transcription');

    } catch (e) {
        console.error('Start recording failed:', e);
        addTerminalLine(`[錯誤] 錄音啟動失敗：${e.message}`, 'error');
        alert('無法啟動錄音：' + e.message);
    }
}

// =============================================================================
// Pause / Resume
// =============================================================================
const ICON_RECORD = '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4" fill="currentColor"/>';
const ICON_PAUSE = '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/>';
const ICON_RESUME = '<polygon points="7,5 19,12 7,19" fill="currentColor"/>';

function setRecordBtnIcon(svgContent) {
    if (!recordBtn) return;
    const svg = recordBtn.querySelector('svg');
    if (svg) svg.innerHTML = svgContent;
}

async function pauseRecording() {
    if (!isRecording || isPaused) return;
    if (audioContext && audioContext.state === 'running') {
        await audioContext.suspend();
    }
    isPaused = true;
    recordBtn.classList.remove('recording-active');
    recordBtn.classList.add('recording-paused');
    setRecordBtnIcon(ICON_RESUME);
    recordBtn.setAttribute('data-tooltip', '繼續錄音');
    setStatus('paused', '已暫停');
    addTerminalLine('⏸ 錄音已暫停', 'warning');
}

async function resumeRecording() {
    if (!isRecording || !isPaused) return;
    if (audioContext && audioContext.state === 'suspended') {
        await audioContext.resume();
    }
    isPaused = false;
    recordBtn.classList.add('recording-active');
    recordBtn.classList.remove('recording-paused');
    setRecordBtnIcon(ICON_PAUSE);
    recordBtn.setAttribute('data-tooltip', '暫停錄音');
    setStatus('recording', '錄音中');
    addTerminalLine('▶ 錄音已恢復', 'success');
}

async function stopRecording() {
    if (!isRecording) return;

    // Resume first if paused, so audioContext can be closed cleanly
    if (isPaused && audioContext && audioContext.state === 'suspended') {
        await audioContext.resume();
    }

    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }
    if (systemStream) {
        systemStream.getTracks().forEach(track => track.stop());
        systemStream = null;
    }

    if (audioContext) {
        await audioContext.close();
        audioContext = null;
    }
    workletNode = null;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'stop' }));
    }

    isRecording = false;
    isPaused = false;
    recordBtn.classList.remove('recording-active', 'recording-paused');
    setRecordBtnIcon(ICON_RECORD);
    recordBtn.setAttribute('data-tooltip', '錄音');
    stopBtn.disabled = true;
    setStatus('connected', '已連線');
    stopTimer();
    updateAudioLevel(0);

    addTerminalLine('■ 錄音已停止。等待摘要生成中...', 'warning');

    // Auto-save meeting after a brief delay for final transcripts
    setTimeout(() => saveMeeting(), 2000);

    // Show loading overlay
    if (summaryLoadingOverlay) summaryLoadingOverlay.classList.add('active');
}

// =============================================================================
// Meeting Persistence (Save / Load / Delete)
// =============================================================================

function formatMeetingDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h${String(m).padStart(2, '0')}m`;
    return `${m}m${String(s).padStart(2, '0')}s`;
}

function extractTitleFromSummary(summaryText) {
    if (!summaryText) return '';
    // New format: first line is "# Meeting Title"
    const h1 = summaryText.match(/^#\s+(.+)/m);
    if (h1) return h1[1].trim().replace(/\*+/g, '');
    // Legacy fallback: 🎯 **主題**：XXXX
    const legacy = summaryText.match(/🎯\s*\**主題\**[：:]\s*(.+)/m);
    if (legacy) return legacy[1].trim().replace(/\*+/g, '');
    // Final fallback: first non-empty line
    const lines = summaryText.split('\n').filter(l => l.trim());
    if (lines.length) return lines[0].replace(/^#+\s*/, '').trim();
    return '';
}

async function saveMeeting() {
    // Collect transcript lines from DOM
    const transcriptEls = document.querySelectorAll('#transcriptionContent .code-line:not(.empty-state .code-line)');
    const transcripts = Array.from(transcriptEls).map(el => el.textContent);

    const translationEls = document.querySelectorAll('#translationContent .code-line:not(.empty-state .code-line)');
    const translations = Array.from(translationEls).map(el => el.textContent);

    const summaryEl = document.getElementById('summaryContent');
    const summary = summaryEl ? summaryEl.innerText : '';

    // Extract title from summary topic line
    const autoTitle = extractTitleFromSummary(summary);

    // Calculate duration
    const duration = startTime ? Math.floor((Date.now() - startTime) / 1000) : 0;

    const body = {
        title: autoTitle,
        duration,
        mode: currentMode,
        transcripts,
        translations,
        summary,
        speakers: speakerMap
    };

    try {
        const res = await fetch('/api/meetings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        _currentMeetingId = data.id;
        addTerminalLine(`[儲存] 會議已儲存: ${data.title}`, 'success');
        loadMeetings();
    } catch (err) {
        addTerminalLine(`[儲存] 儲存失敗: ${err.message}`, 'error');
    }
}

async function loadMeetings() {
    const list = document.getElementById('meetingsList');
    if (!list) return;

    try {
        const res = await fetch('/api/meetings');
        const meetings = await res.json();

        if (!meetings.length) {
            list.innerHTML = '<div class="tree-item meeting-empty"><span class="tree-filename comment-text">// 尚無會議紀錄</span></div>';
            return;
        }

        list.innerHTML = meetings.map(m => {
            const date = new Date(m.created_at);
            const dateStr = `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
            const dur = formatMeetingDuration(m.duration);
            const displayTitle = escapeHtml(m.title || '未命名會議');
            return `<div class="meeting-item" data-id="${m.id}" onclick="loadMeeting('${m.id}')">
                <div class="meeting-item__header">
                    <svg class="meeting-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
                    </svg>
                    <span class="meeting-meta">${dateStr} · ${dur}</span>
                    <button class="meeting-delete" onclick="event.stopPropagation(); deleteMeeting('${m.id}')" title="刪除">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                        </svg>
                    </button>
                </div>
                <div class="meeting-title">${displayTitle}</div>
            </div>`;
        }).join('');
    } catch (err) {
        console.error('[Meetings] Load failed:', err);
    }
}

async function loadMeeting(meetingId) {
    try {
        const res = await fetch(`/api/meetings/${meetingId}`);
        if (!res.ok) throw new Error('Not found');
        const m = await res.json();
        _currentMeetingId = meetingId;
        // Ensure we're in preview mode
        switchSummaryMode(false);

        // Restore transcripts
        const transcriptionContent = document.getElementById('transcriptionContent');
        if (transcriptionContent && m.transcripts.length) {
            transcriptionContent.innerHTML = '';
            lineCounter = 0;
            m.transcripts.forEach(line => {
                lineCounter++;
                const div = document.createElement('div');
                div.className = 'code-line';
                div.innerHTML = `<span class="ln">${lineCounter}</span><span class="code-text">${line}</span>`;
                transcriptionContent.appendChild(div);
            });
        }

        // Restore translations
        const translationContent = document.getElementById('translationContent');
        if (translationContent && m.translations.length) {
            translationContent.innerHTML = '';
            translationLineCounter = 0;
            m.translations.forEach(line => {
                translationLineCounter++;
                const div = document.createElement('div');
                div.className = 'code-line';
                div.innerHTML = `<span class="ln">${translationLineCounter}</span><span class="code-text">${line}</span>`;
                translationContent.appendChild(div);
            });
        }

        // Restore summary (render as markdown)
        if (m.summary) {
            renderSummaryText(m.summary);
        }

        // Restore speakers
        if (m.speakers && Object.keys(m.speakers).length) {
            speakerMap = m.speakers;
            updateSpeakerTreeList();
            renderSpeakerEditor();
        }

        // Switch to transcription view
        switchTab('transcription');
        addTerminalLine(`[載入] 已載入: ${m.title}`, 'info');

        // Highlight selected item
        document.querySelectorAll('.meeting-item').forEach(el => {
            el.classList.toggle('active', el.dataset.id === meetingId);
        });
    } catch (err) {
        addTerminalLine(`[載入] 載入失敗: ${err.message}`, 'error');
    }
}

async function deleteMeeting(meetingId) {
    if (!confirm('確定要刪除這筆會議紀錄嗎？')) return;

    try {
        const res = await fetch(`/api/meetings/${meetingId}`, { method: 'DELETE' });
        if (res.ok) {
            addTerminalLine('[刪除] 會議紀錄已刪除', 'success');
            loadMeetings();
        }
    } catch (err) {
        addTerminalLine(`[刪除] 刪除失敗: ${err.message}`, 'error');
    }
}

// =============================================================================
// Init — Event Bindings & Startup
// =============================================================================
document.addEventListener('DOMContentLoaded', () => {

    // ── Core action buttons ──
    if (recordBtn) {
        recordBtn.addEventListener('click', () => {
            if (!isRecording) {
                startRecording();
            } else if (isPaused) {
                resumeRecording();
            } else {
                pauseRecording();
            }
        });
    }
    if (stopBtn) {
        stopBtn.addEventListener('click', () => {
            if (isRecording) stopRecording();
        });
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            clearAll();
            addTerminalLine('✓ 已清除所有內容', 'success');
        });
    }
    if (exportBtn) {
        exportBtn.addEventListener('click', () => {
            exportMarkdown();
        });
    }

    // ── Mode toggle (zh ↔ en-translate) ──
    if (modeToggleBtn) {
        modeToggleBtn.addEventListener('click', () => {
            const newMode = currentMode === 'zh' ? 'en-translate' : 'zh';
            setMode(newMode);
        });
    }

    // ── Settings modal ──
    if (settingsBtn) {
        settingsBtn.addEventListener('click', () => {
            if (settingsModal) settingsModal.classList.add('active');
            loadAudioDevices();
        });
    }
    if (closeSettings) {
        closeSettings.addEventListener('click', () => {
            if (settingsModal) settingsModal.classList.remove('active');
        });
    }
    if (settingsModal) {
        settingsModal.addEventListener('click', (e) => {
            if (e.target === settingsModal) settingsModal.classList.remove('active');
        });
    }

    // ── Summary modal ──
    if (closeSummary) {
        closeSummary.addEventListener('click', () => {
            if (summaryModal) summaryModal.classList.remove('active');
        });
    }
    if (summaryModal) {
        summaryModal.addEventListener('click', (e) => {
            if (e.target === summaryModal) summaryModal.classList.remove('active');
        });
    }
    if (copySummary) {
        copySummary.addEventListener('click', () => copySummaryText());
    }
    if (downloadSummary) {
        downloadSummary.addEventListener('click', () => downloadSummaryMd());
    }

    // ── Summary toolbar (edit/preview/highlight/save) ──
    const summaryPreviewBtn = document.getElementById('summaryPreviewBtn');
    const summaryEditBtn = document.getElementById('summaryEditBtn');
    const summaryHighlightBtn = document.getElementById('summaryHighlightBtn');
    const summarySaveBtn = document.getElementById('summarySaveBtn');

    if (summaryPreviewBtn) {
        summaryPreviewBtn.addEventListener('click', () => switchSummaryMode(false));
    }
    if (summaryEditBtn) {
        summaryEditBtn.addEventListener('click', () => switchSummaryMode(true));
    }
    if (summaryHighlightBtn) {
        summaryHighlightBtn.addEventListener('click', () => highlightSelection());
    }
    if (summarySaveBtn) {
        summarySaveBtn.addEventListener('click', () => saveSummaryEdits());
    }
    // ── Startup ──
    connectWebSocket();
    switchTab('transcription');
    loadMeetings();
    addTerminalLine('[系統] ConCall S94 Code Editor Pro 已初始化', 'info');
    console.log('[ConCall] S94 Code Editor Pro initialized');
});
