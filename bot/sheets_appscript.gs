/**
 * Apps Script web app do bot de CTA (Ponto 4).
 *
 * COMO INSTALAR:
 *   1. Abra a planilha → Extensões → Apps Script.
 *   2. Apague o conteúdo do Code.gs e cole TUDO isto.
 *   3. (Opcional) Defina um SECRET abaixo e o MESMO valor no .env do bot
 *      (SHEETS_SECRET=...). Se deixar vazio, qualquer um com a URL pode chamar.
 *   4. Implantar → Nova implantação → Tipo: App da Web.
 *        · Executar como: Eu
 *        · Quem tem acesso: Qualquer pessoa
 *   5. Copie a URL /exec e ponha em SHEETS_WEBHOOK_URL no .env (já está lá).
 *      Se você reimplanta numa NOVA implantação, a URL muda — atualize o .env.
 *
 * ESTRUTURA ESPERADA DA PÁGINA "COMPS":
 *   Coluna A = nome da comp        (ex: "ZvZ Padrão")
 *   Coluna B = nome da página-MODELO a ser copiada para cada CTA dessa comp
 *   Coluna C = nome da página que contém as ROLES dessa comp (roles na coluna A)
 *
 * LISTA DE INSCRITOS (colunas T:U:V:W = nome + 3 roles) — MODELO PERSISTENTE:
 *   Quem pingou NUNCA some da lista; muda só de seção/cor conforme o estado.
 *   A fonte da verdade é um REGISTRO-MESTRE OCULTO (colunas AJ:AN), e a lista
 *   visível T:W é RENDERIZADA a partir dele. Estados:
 *     · esperando (pingou, fora da escalação A) → topo, cor padrão;
 *     · escalado  (nome aparece na coluna A)     → fim da lista, VERDE;
 *     · cancelado (cancelou o ping pelo bot)      → rodapé, CINZA, não escala.
 *   Há GAP_ROWS linhas em branco entre o último "esperando" e o bloco de baixo
 *   (só quando há ambos). Apagar um nome à mão (de T:W e/ou A) é desfeito no
 *   próximo render — o nome VOLTA; só o cancelamento (cinza) tira da rotação.
 */

// ===================== CONFIGURAÇÃO =====================
var SECRET            = '';        // igual ao SHEETS_SECRET do bot (vazio = sem checagem)
var COMPS_SHEET       = 'COMPS';
var COMPS_HEADER_ROWS = 0;         // nº de linhas de cabeçalho na página COMPS
var ROLES_HEADER_ROWS = 0;         // nº de linhas de cabeçalho na página de roles
var COMP_COL          = 1;         // A: nome da comp
var TEMPLATE_COL      = 2;         // B: página-modelo a copiar
var ROLES_PAGE_COL    = 3;         // C: página de roles
var ROLES_COL         = 1;         // A: coluna das roles na página de roles
var SUBMIT_START_COL  = 20;        // T: 1ª coluna da lista (T=nome, U,V,W=roles)
var POOL_HEADER_ROWS  = 0;         // nº de linhas de cabeçalho na lista T:W
// --- Área de escalação (auto-preenchimento / party leader) ---
var ASSIGN_FIRST_ROW  = 3;         // 1ª linha de slot (A3/B3...)
var ASSIGN_NAME_COL   = 1;         // A: nome do jogador escalado
var ASSIGN_ROLE_COL   = 2;         // B: role exigida pela comp naquele slot
var PARTY_SIZE        = 20;        // jogadores por party (linhas 3-22, 23-42, ...)
var BUTTON_ROW        = 2;         // linha do checkbox-botão (A2)
var BUTTON_COL        = 1;         // coluna do checkbox-botão (A2)
// --- Party leader (prioridade pelo ícone inicial da role em B) ---
var LEADER_PRIORITY   = ['🛡️', '🛻', '✨', '⚔️', '🕊️'];  // ordem: 1º = maior prioridade
var CROWN             = '👑';       // marcador do party leader (prefixo + negrito)
// --- Registro-mestre OCULTO da lista (fonte da verdade) ---
// AJ:AN = [nome, role1, role2, role3, cancelado(1/'')], 1 linha por inscrito.
// Use colunas livres à direita. As colunas ficam ocultas automaticamente.
var MASTER_COL        = 36;        // AJ: 1ª coluna do registro-mestre
var MASTER_WIDTH      = 5;         // AJ..AN
var MASTER_FIRST_ROW  = 1;
// --- Cores e espaçamento da lista visível T:W ---
var COLOR_WAITING     = '#000000'; // esperando (cor padrão)
var COLOR_ESCALATED   = '#0b8043'; // escalado (verde)
var COLOR_CANCELLED   = '#b7b7b7'; // cancelado (cinza-claro)
var GAP_ROWS          = 3;         // linhas em branco entre "esperando" e o bloco de baixo
// ========================================================


function doGet(e) {
  return _json({ ok: true, msg: 'CTA bot web app ativo. Use POST com action.' });
}

function doPost(e) {
  var req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return _json({ ok: false, error: 'corpo inválido (esperado JSON)' });
  }

  if (SECRET && req.secret !== SECRET) {
    return _json({ ok: false, error: 'secret inválido' });
  }

  var action = req.action || '';

  try {
    switch (action) {
      case 'list_comps':       return _json(listComps());
      case 'create_cta_page':  return _json(createCtaPage(req));
      case 'list_roles':       return _json(listRoles(req));
      case 'submit_functions': return _json(submitFunctions(req));
      case 'cancel_functions': return _json(cancelFunctions(req));
      case 'remove_functions': return _json(removeFunctions(req));
      case 'delete_cta_page':  return _json(deleteCtaPage(req));
      case 'get_assignments':  return _json(getAssignments(req));
      case '':                 return _json(legacyAppend(req));  // compat mass-info antigo
      default:                 return _json({ ok: false, error: 'action desconhecida: ' + action });
    }
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  }
}


// ===================== AÇÕES =====================

function listComps() {
  var sh = _sheet(COMPS_SHEET);
  var last = sh.getLastRow();
  var out = [];
  if (last > COMPS_HEADER_ROWS) {
    var vals = sh.getRange(COMPS_HEADER_ROWS + 1, COMP_COL, last - COMPS_HEADER_ROWS, 1).getValues();
    for (var i = 0; i < vals.length; i++) {
      var v = String(vals[i][0]).trim();
      if (v) out.push(v);
    }
  }
  return { ok: true, comps: out };
}

function createCtaPage(req) {
  var comp = String(req.comp || '').trim();
  var pageName = String(req.page_name || '').trim();
  var eventId = req.event_id;
  if (!comp || !pageName) return { ok: false, error: 'comp e page_name são obrigatórios' };

  var row = _findCompRow(comp);
  if (!row) return { ok: false, error: 'comp não encontrada na página COMPS: ' + comp };

  var templateName = String(row[TEMPLATE_COL - 1]).trim();
  if (!templateName) return { ok: false, error: 'coluna B (modelo) vazia para a comp ' + comp };

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var template = ss.getSheetByName(templateName);
  if (!template) return { ok: false, error: 'página-modelo não existe: ' + templateName };

  var finalName = _uniqueSheetName(pageName, eventId);
  var copy = template.copyTo(ss);
  copy.setName(finalName);
  // copyTo pode revelar o template (comportamento do Sheets ao copiar aba oculta
  // dentro da mesma planilha) — garante que ele volte a ficar oculto.
  template.hideSheet();
  // A cópia herda o estado oculto do template; a página do CTA precisa aparecer.
  copy.showSheet();
  // Move a cópia para o fim (opcional, só pra organização)
  ss.setActiveSheet(copy);
  ss.moveActiveSheet(ss.getNumSheets());

  // Deep link para a aba recém-criada: URL da planilha + #gid=<id da aba>.
  var sheetUrl = ss.getUrl() + '#gid=' + copy.getSheetId();

  return { ok: true, page_name: finalName, sheet_url: sheetUrl };
}

function listRoles(req) {
  var comp = String(req.comp || '').trim();
  if (!comp) return { ok: false, error: 'comp é obrigatória' };

  var row = _findCompRow(comp);
  if (!row) return { ok: false, error: 'comp não encontrada: ' + comp };

  var rolesPageName = String(row[ROLES_PAGE_COL - 1]).trim();
  if (!rolesPageName) return { ok: false, error: 'coluna C (página de roles) vazia para ' + comp };

  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(rolesPageName);
  if (!sh) return { ok: false, error: 'página de roles não existe: ' + rolesPageName };

  var last = sh.getLastRow();
  var out = [];
  if (last > ROLES_HEADER_ROWS) {
    var vals = sh.getRange(ROLES_HEADER_ROWS + 1, ROLES_COL, last - ROLES_HEADER_ROWS, 1).getValues();
    for (var i = 0; i < vals.length; i++) {
      var v = String(vals[i][0]).trim();
      if (v) out.push(v);
    }
  }
  return { ok: true, roles: out };
}

/**
 * Registra (ou re-registra) um jogador. UPSERT no registro-mestre por nome:
 * grava as roles novas, REATIVA (tira o "cancelado") e tira o nome da escalação
 * (coluna A) — assim quem re-registra volta a "esperar" com as novas roles.
 * `prev_name` (nick antigo) é removido do mestre se mudou. Render no fim.
 */
function submitFunctions(req) {
  var pageName = String(req.page_name || '').trim();
  if (!pageName) return { ok: false, error: 'page_name é obrigatório' };
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(pageName);
  if (!sh) return { ok: false, error: 'página do CTA não existe: ' + pageName };

  var name = String(req.discord_name || '').trim();
  if (!name) return { ok: false, error: 'discord_name é obrigatório' };
  var prefs = [];
  for (var k = 1; k <= 3; k++) { var r = String(req['role' + k] || '').trim(); if (r) prefs.push(r); }
  var prevName = String(req.prev_name || '').trim();

  var entries = _readMaster(sh);

  // Troca de nick: remove a entrada antiga (e tira da escalação).
  if (prevName && prevName.toLowerCase() !== name.toLowerCase()) {
    var pi = _masterIndexOf(entries, prevName);
    if (pi !== -1) entries.splice(pi, 1);
    _removeFromAssignment(sh, prevName);
  }

  // Upsert + reativa.
  var idx = _masterIndexOf(entries, name);
  if (idx === -1) entries.push({ name: name, prefs: prefs, cancelled: false });
  else { entries[idx].name = name; entries[idx].prefs = prefs; entries[idx].cancelled = false; }

  _removeFromAssignment(sh, name);   // re-registrar tira da escalação (volta a esperar)
  _writeMaster(sh, entries);
  _renderPool(sh);
  if (_isCtaPage(sh) && _isButtonOn(sh)) autofillRoles(sh);

  return { ok: true, row: 0 };
}

/**
 * Cancela o ping de um jogador: marca CANCELADO no mestre (sticky), tira o nome
 * da escalação (A) e renderiza — o nome fica CINZA no rodapé de T:W e o autofill
 * deixa de escalá-lo. Não some da lista (registro persistente).
 */
function cancelFunctions(req) {
  var pageName = String(req.page_name || '').trim();
  if (!pageName) return { ok: false, error: 'page_name é obrigatório' };
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(pageName);
  if (!sh) return { ok: false, error: 'página do CTA não existe: ' + pageName };
  var name = String(req.discord_name || '').trim();
  if (!name) return { ok: false, error: 'discord_name é obrigatório' };

  var entries = _readMaster(sh);
  var idx = _masterIndexOf(entries, name);
  if (idx === -1) { _renderPool(sh); return { ok: true, note: 'não estava registrado' }; }
  entries[idx].cancelled = true;
  _removeFromAssignment(sh, name);
  _writeMaster(sh, entries);
  _renderPool(sh);
  return { ok: true };
}

/**
 * Remove DE VEZ um jogador (hard remove): apaga do registro-mestre, tira da
 * escalação e renderiza. Mantido para uso manual/compat — o fluxo normal usa
 * submit (upsert) e cancel (cinza).
 */
function removeFunctions(req) {
  var pageName = String(req.page_name || '').trim();
  if (!pageName) return { ok: false, error: 'page_name é obrigatório' };
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(pageName);
  if (!sh) return { ok: false, error: 'página do CTA não existe: ' + pageName };
  var name = String(req.discord_name || '').trim();
  if (!name) return { ok: false, error: 'discord_name é obrigatório' };

  var entries = _readMaster(sh);
  var idx = _masterIndexOf(entries, name);
  if (idx !== -1) entries.splice(idx, 1);
  _removeFromAssignment(sh, name);
  _writeMaster(sh, entries);
  _renderPool(sh);
  return { ok: true, cleared: idx !== -1 ? 1 : 0 };
}

/**
 * Apaga a página (aba) de um CTA pelo nome exato.
 * Idempotente: se a aba já não existe, devolve ok:true (deleted:false).
 * O Google não permite apagar a ÚLTIMA aba da planilha — nesse caso recusa.
 */
function deleteCtaPage(req) {
  var pageName = String(req.page_name || '').trim();
  if (!pageName) return { ok: false, error: 'page_name é obrigatório' };

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(pageName);
  if (!sh) return { ok: true, deleted: false };  // já não existe
  if (ss.getNumSheets() <= 1) {
    return { ok: false, error: 'não dá pra apagar a única aba da planilha' };
  }
  ss.deleteSheet(sh);
  return { ok: true, deleted: true };
}

/** Compat: chamada antiga do mass-info (sem action). Apenas confirma sem quebrar. */
function legacyAppend(req) {
  if (!req.discord_name) return { ok: false, error: 'sem action' };
  return { ok: true, legacy: true };
}


// ============ REGISTRO-MESTRE (fonte da verdade da lista T:W) ============

/** Lê o registro-mestre (AJ:AN): [{name, prefs:[..], cancelled:bool}], em ordem. */
function _readMaster(sheet) {
  var out = [];
  if (sheet.getMaxColumns() < MASTER_COL + MASTER_WIDTH - 1) return out;
  var lastRow = sheet.getLastRow();
  if (lastRow < MASTER_FIRST_ROW) return out;
  var n = lastRow - MASTER_FIRST_ROW + 1;
  var vals = sheet.getRange(MASTER_FIRST_ROW, MASTER_COL, n, MASTER_WIDTH).getValues();
  for (var i = 0; i < vals.length; i++) {
    var name = String(vals[i][0]).trim();
    if (!name) continue;
    var prefs = [];
    for (var k = 1; k <= 3; k++) { var r = String(vals[i][k]).trim(); if (r) prefs.push(r); }
    out.push({ name: name, prefs: prefs, cancelled: String(vals[i][4]).trim() !== '' });
  }
  return out;
}

/** Última linha NÃO vazia do registro-mestre (coluna do nome), ou MASTER_FIRST_ROW-1. */
function _masterLastRow(sheet) {
  if (sheet.getMaxColumns() < MASTER_COL) return MASTER_FIRST_ROW - 1;
  var lastRow = sheet.getLastRow();
  if (lastRow < MASTER_FIRST_ROW) return MASTER_FIRST_ROW - 1;
  var vals = sheet.getRange(MASTER_FIRST_ROW, MASTER_COL, lastRow - MASTER_FIRST_ROW + 1, 1).getValues();
  var last = MASTER_FIRST_ROW - 1;
  for (var i = 0; i < vals.length; i++) {
    if (String(vals[i][0]).trim() !== '') last = MASTER_FIRST_ROW + i;
  }
  return last;
}

/** Grava o registro-mestre (substitui tudo, limpando sobras) e oculta as colunas. */
function _writeMaster(sheet, entries) {
  if (!_ensureColumns(sheet, MASTER_COL + MASTER_WIDTH - 1)) return;
  var prevCount = Math.max(0, _masterLastRow(sheet) - MASTER_FIRST_ROW + 1);
  var rows = Math.max(entries.length, prevCount, 1);
  var out = [];
  for (var i = 0; i < rows; i++) {
    var e = entries[i];
    if (e) out.push([e.name, e.prefs[0] || '', e.prefs[1] || '', e.prefs[2] || '', e.cancelled ? 1 : '']);
    else out.push(['', '', '', '', '']);
  }
  sheet.getRange(MASTER_FIRST_ROW, MASTER_COL, rows, MASTER_WIDTH).setValues(out);
  try { sheet.hideColumns(MASTER_COL, MASTER_WIDTH); } catch (err) {}
}

/** Índice (case/trim-insensitive) de um nome no registro-mestre, ou -1. */
function _masterIndexOf(entries, name) {
  var needle = String(name).trim().toLowerCase();
  for (var i = 0; i < entries.length; i++) {
    if (entries[i].name.trim().toLowerCase() === needle) return i;
  }
  return -1;
}


// ============ RENDER da lista visível T:W (a partir do mestre) ============
/**
 * Reconstrói a lista T:W a partir do registro-mestre + da escalação (A):
 *   · adota nomes digitados à mão em T:W que não estão no mestre;
 *   · classifica cada inscrito (esperando / escalado / cancelado);
 *   · escreve as seções: esperando · (GAP_ROWS linhas) · escalado(verde) ·
 *     cancelado(cinza), aplicando a cor da fonte por linha.
 * Como T:W é sempre reconstruída do mestre, apagar nomes à mão é desfeito aqui
 * (restauração automática); só o "cancelado" tira de fato da rotação.
 */
function _renderPool(sheet) {
  if (!_isCtaPage(sheet)) return;

  // 1) Adoção: nomes visíveis em T:W ainda não presentes no mestre viram inscritos.
  var entries = _readMaster(sheet);
  var visible = _readVisiblePool(sheet);
  var adopted = false;
  for (var i = 0; i < visible.length; i++) {
    if (_masterIndexOf(entries, visible[i].name) === -1) {
      entries.push({ name: visible[i].name, prefs: visible[i].prefs, cancelled: false });
      adopted = true;
    }
  }

  // 2) Classifica (escalado = nome presente na coluna A).
  var assigned = _assignmentNameSet(sheet);
  var waiting = [], escalated = [], cancelled = [];
  for (var j = 0; j < entries.length; j++) {
    var e = entries[j];
    if (e.cancelled) cancelled.push(e);
    else if (assigned[e.name.trim().toLowerCase()]) escalated.push(e);
    else waiting.push(e);
  }

  // 3) Monta linhas + cores.
  var rows = [], colors = [];
  function pushEntry(e, color) {
    rows.push([e.name, e.prefs[0] || '', e.prefs[1] || '', e.prefs[2] || '']);
    colors.push([color, color, color, color]);
  }
  function pushBlank() {
    rows.push(['', '', '', '']);
    colors.push([COLOR_WAITING, COLOR_WAITING, COLOR_WAITING, COLOR_WAITING]);
  }
  for (var w = 0; w < waiting.length; w++) pushEntry(waiting[w], COLOR_WAITING);
  if (waiting.length > 0 && (escalated.length + cancelled.length) > 0) {
    for (var g = 0; g < GAP_ROWS; g++) pushBlank();
  }
  for (var s = 0; s < escalated.length; s++) pushEntry(escalated[s], COLOR_ESCALATED);
  for (var c = 0; c < cancelled.length; c++) pushEntry(cancelled[c], COLOR_CANCELLED);

  // 4) Escreve T:W (limpa a área antiga, padroniza o resto em branco).
  var startRow = POOL_HEADER_ROWS + 1;
  var lastRow = sheet.getLastRow();
  var oldLen = Math.max(0, lastRow - POOL_HEADER_ROWS);
  var total = Math.max(rows.length, oldLen, 1);
  var outVals = [], outColors = [];
  for (var t = 0; t < total; t++) {
    if (t < rows.length) { outVals.push(rows[t]); outColors.push(colors[t]); }
    else {
      outVals.push(['', '', '', '']);
      outColors.push([COLOR_WAITING, COLOR_WAITING, COLOR_WAITING, COLOR_WAITING]);
    }
  }
  var rng = sheet.getRange(startRow, SUBMIT_START_COL, total, 4);
  rng.setValues(outVals);
  rng.setFontColors(outColors);

  // 5) Persiste o mestre se adotou alguém.
  if (adopted) _writeMaster(sheet, entries);
}

/** Lê a lista visível T:W: [{name, prefs:[..]}], só linhas com nome. */
function _readVisiblePool(sheet) {
  var out = [];
  var lastRow = sheet.getLastRow();
  if (lastRow <= POOL_HEADER_ROWS) return out;
  var vals = sheet.getRange(POOL_HEADER_ROWS + 1, SUBMIT_START_COL, lastRow - POOL_HEADER_ROWS, 4).getValues();
  for (var i = 0; i < vals.length; i++) {
    var name = String(vals[i][0]).trim();
    if (!name) continue;
    var prefs = [];
    for (var k = 1; k <= 3; k++) { var r = String(vals[i][k]).trim(); if (r) prefs.push(r); }
    out.push({ name: name, prefs: prefs });
  }
  return out;
}

/** Set de nomes (lower, sem coroa) presentes na escalação (coluna A). */
function _assignmentNameSet(sheet) {
  var set = {};
  var firstRow = ASSIGN_FIRST_ROW;
  var lastRow = sheet.getLastRow();
  if (lastRow < firstRow) return set;
  var vals = sheet.getRange(firstRow, ASSIGN_NAME_COL, lastRow - firstRow + 1, 1).getValues();
  for (var i = 0; i < vals.length; i++) {
    var nm = _stripCrown(vals[i][0]);
    if (nm) set[nm.toLowerCase()] = true;
  }
  return set;
}

/** Linha da escalação (A) onde está `name` (ignora coroa), ou -1. */
function _findAssignmentRow(sheet, name) {
  var firstRow = ASSIGN_FIRST_ROW;
  var lastRow = sheet.getLastRow();
  if (lastRow < firstRow) return -1;
  var vals = sheet.getRange(firstRow, ASSIGN_NAME_COL, lastRow - firstRow + 1, 1).getValues();
  var needle = String(name).trim().toLowerCase();
  for (var i = 0; i < vals.length; i++) {
    if (_stripCrown(vals[i][0]).toLowerCase() === needle) return firstRow + i;
  }
  return -1;
}

/** Tira o nome da escalação (coluna A) e recalcula os leaders, se estava lá. */
function _removeFromAssignment(sheet, name) {
  var row = _findAssignmentRow(sheet, name);
  if (row > 0) {
    sheet.getRange(row, ASSIGN_NAME_COL).clearContent().setFontWeight('normal');
    assignLeaders(sheet);
  }
}

/** True se o checkbox-botão (A2) está LIGADO nesta página. */
function _isButtonOn(sheet) {
  return sheet.getRange(BUTTON_ROW, BUTTON_COL).getValue() === true;
}

/** True se a aba é uma página de CTA (cópia do modelo), não uma página de sistema. */
function _isCtaPage(sheet) {
  var name = sheet.getName();
  if (name === COMPS_SHEET) return false;
  return _systemPageNames().indexOf(name) === -1;
}

/** Nomes das páginas de sistema: modelos (col B) e páginas de roles (col C) do COMPS. */
function _systemPageNames() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(COMPS_SHEET);
  if (!sh) return [];
  var last = sh.getLastRow();
  if (last <= COMPS_HEADER_ROWS) return [];
  var vals = sh.getRange(COMPS_HEADER_ROWS + 1, 1, last - COMPS_HEADER_ROWS, 3).getValues();
  var out = [];
  for (var i = 0; i < vals.length; i++) {
    var tmpl  = String(vals[i][1]).trim();  // B = modelo
    var roles = String(vals[i][2]).trim();  // C = página de roles
    if (tmpl)  out.push(tmpl);
    if (roles) out.push(roles);
  }
  return out;
}


// ============ ONEDIT: reage a edições MANUAIS (render/escala/leader) ============
/**
 * Gatilho SIMPLES de edição (basta salvar o script). Edições do BOT (via web app)
 * NÃO disparam onEdit — então isto só reage a edições manuais:
 *   · checkbox A2 ligado            → autofill;
 *   · mexeu na coluna A (escalação) → reconcilia (restaura/recolore) + leader/autofill;
 *   · mexeu na lista T:W            → render (adota/restaura/recolore) + autofill se A2 on.
 */
function onEdit(e) {
  try {
    if (!e || !e.range) return;
    var sheet = e.range.getSheet();
    if (sheet.getName() === COMPS_SHEET || !_isCtaPage(sheet)) return;

    var col = e.range.getColumn();
    var lastCol = e.range.getLastColumn();

    if (e.range.getRow() === BUTTON_ROW && col === BUTTON_COL && e.range.getValue() === true) {
      autofillRoles(sheet);   // A2 ligado
      return;
    }

    var touchedAssign = (col <= ASSIGN_NAME_COL && lastCol >= ASSIGN_NAME_COL &&
                         e.range.getLastRow() >= ASSIGN_FIRST_ROW);
    var touchedPool = !(lastCol < SUBMIT_START_COL || col > SUBMIT_START_COL + 3);

    if (touchedAssign) {
      if (_isButtonOn(sheet)) autofillRoles(sheet);  // já renderiza no fim
      else { assignLeaders(sheet); _renderPool(sheet); }
    } else if (touchedPool) {
      _renderPool(sheet);
      if (_isButtonOn(sheet)) autofillRoles(sheet);
    }
  } catch (err) {
    // Nunca interrompe a edição do usuário por erro do script.
  }
}


// ============ AUTO-PREENCHIMENTO da escalação (checkbox A2) ============
/**
 * Distribui os inscritos ESPERANDO (mestre: não cancelados e ainda não em A) nos
 * slots de role da comp (coluna B), por ordem de linha (fecha party por party).
 * Para cada slot vago, escolhe o disponível cuja role escolhida bate com a do
 * slot, preferindo a escolha mais alta (1ª > 2ª > 3ª). Renderiza no fim (os
 * escalados ficam verdes no rodapé de T:W). Cancelados NUNCA são escalados.
 */
function autofillRoles(sheet) {
  var entries = _readMaster(sheet);
  var slots = _readSlots(sheet);

  var taken = {};
  for (var i = 0; i < slots.length; i++) {
    if (slots[i].name) taken[_stripCrown(slots[i].name).toLowerCase()] = true;
  }
  var available = [];
  for (var i = 0; i < entries.length; i++) {
    var e = entries[i];
    if (e.cancelled) continue;
    if (taken[e.name.trim().toLowerCase()]) continue;
    available.push({ name: e.name, prefs: e.prefs });
  }

  for (var i = 0; i < slots.length; i++) {
    var s = slots[i];
    if (s.name || !s.role) continue;
    var best = null, bestRank = 999, bestIdx = -1;
    for (var j = 0; j < available.length; j++) {
      var rank = _indexOfRole(available[j].prefs, s.role);
      if (rank !== -1 && rank < bestRank) { best = available[j]; bestRank = rank; bestIdx = j; }
    }
    if (best) { s.name = best.name; available.splice(bestIdx, 1); }
  }

  _writeAssignments(sheet, slots);
  assignLeaders(sheet);
  _renderPool(sheet);   // recolore a lista (escalados → verde no fim)
}

/** Garante que a aba tem ao menos `lastCol` colunas. Retorna true se conseguiu. */
function _ensureColumns(sheet, lastCol) {
  var max = sheet.getMaxColumns();
  if (max >= lastCol) return true;
  try {
    sheet.insertColumnsAfter(max, lastCol - max);
  } catch (err) {
    return false;
  }
  return sheet.getMaxColumns() >= lastCol;
}

/** Lê os slots da escalação (A=nome, B=role) a partir de ASSIGN_FIRST_ROW.
 *  Só conta como slot a linha que tem role (B) preenchida. */
function _readSlots(sheet) {
  var firstRow = ASSIGN_FIRST_ROW;
  var lastRow = sheet.getLastRow();
  var out = [];
  if (lastRow < firstRow) return out;
  var numRows = lastRow - firstRow + 1;
  var vals = sheet.getRange(firstRow, ASSIGN_NAME_COL, numRows, 2).getValues(); // A,B
  for (var i = 0; i < vals.length; i++) {
    var role = String(vals[i][1]).trim();  // B
    if (!role) continue;
    out.push({ row: firstRow + i, role: role, name: String(vals[i][0]).trim() }); // A
  }
  return out;
}

/** Escreve os nomes de volta na coluna A, preservando linhas que não são slot. */
function _writeAssignments(sheet, slots) {
  var firstRow = ASSIGN_FIRST_ROW;
  var lastRow = sheet.getLastRow();
  if (lastRow < firstRow) return;
  var numRows = lastRow - firstRow + 1;
  var rng = sheet.getRange(firstRow, ASSIGN_NAME_COL, numRows, 1);  // coluna A
  var col = rng.getValues();
  for (var i = 0; i < slots.length; i++) {
    col[slots[i].row - firstRow][0] = slots[i].name;
  }
  rng.setValues(col);
}

/** Índice (case/trim-insensitive) de uma role dentro de um array de prefs; -1 se ausente. */
function _indexOfRole(arr, role) {
  var needle = String(role).trim().toLowerCase();
  for (var i = 0; i < arr.length; i++) {
    if (String(arr[i]).trim().toLowerCase() === needle) return i;
  }
  return -1;
}


// ============ PARTY LEADER por party (👑 + negrito) ============
/**
 * Define 1 party leader por party. Para cada bloco de PARTY_SIZE linhas, escolhe
 * entre os slots PREENCHIDOS o de maior prioridade pelo ícone da role (B):
 * 🛡️ > 🛻 > ✨ > ⚔️ > 🕊️. Empate de ícone => primeiro (linha menor).
 * Marca o leader com 👑 + negrito; remove a marca dos demais. Idempotente.
 */
function assignLeaders(sheet) {
  var firstRow = ASSIGN_FIRST_ROW;
  var lastRow = sheet.getLastRow();
  if (lastRow < firstRow) return;
  var numRows = lastRow - firstRow + 1;

  var vals = sheet.getRange(firstRow, ASSIGN_NAME_COL, numRows, 2).getValues(); // A, B
  var nameRng = sheet.getRange(firstRow, ASSIGN_NAME_COL, numRows, 1);          // só A
  var curWeights = nameRng.getFontWeights();

  // 1) Acha o slot leader de cada party.
  var bestPrio = {}, leaderOff = {};
  for (var i = 0; i < numRows; i++) {
    var role = String(vals[i][1]).trim();
    if (!role) continue;
    var plain = _stripCrown(vals[i][0]);
    if (!plain) continue;
    var party = Math.floor(i / PARTY_SIZE);
    var prio = _leaderRank(role);
    if (!(party in bestPrio) || prio < bestPrio[party]) {
      bestPrio[party] = prio;
      leaderOff[party] = i;
    }
  }

  // 2) Reescreve a coluna A: tira coroa de todos, põe no leader; ajusta negrito.
  var names = [], weights = [];
  for (var i = 0; i < numRows; i++) {
    var role = String(vals[i][1]).trim();
    if (!role) {                       // não é slot: preserva valor e formato
      names.push([vals[i][0]]);
      weights.push([curWeights[i][0]]);
      continue;
    }
    var plain = _stripCrown(vals[i][0]);
    var party = Math.floor(i / PARTY_SIZE);
    if (!plain) {                      // slot vago
      names.push(['']);
      weights.push(['normal']);
    } else if (leaderOff[party] === i) {  // leader da party
      names.push([CROWN + ' ' + plain]);
      weights.push(['bold']);
    } else {                           // membro comum
      names.push([plain]);
      weights.push(['normal']);
    }
  }
  nameRng.setValues(names);
  nameRng.setFontWeights(weights);
}

/** Remove a coroa (e espaços) do início de um nome. */
function _stripCrown(name) {
  return String(name).replace(/^\s*👑\s*/, '').trim();
}

/** Prioridade do leader pelo ícone inicial da role (menor = maior prioridade). */
function _leaderRank(role) {
  var tok = String(role).trim().split(/\s+/)[0] || '';
  tok = tok.replace(/️/g, '');                 // normaliza variation selectors
  for (var i = 0; i < LEADER_PRIORITY.length; i++) {
    if (LEADER_PRIORITY[i].replace(/️/g, '') === tok) return i;
  }
  return 999;                                       // ícone fora da lista = menor prioridade
}


// ============ LEITURA da escalação para o bot (PMs) ============
/** Junta tier + nome de uma peça num texto só: "8.4 Bruxa". Se faltar um dos dois,
 *  usa o que tiver. Vazio se ambos vazios. */
function _combineTierName(tier, name) {
  var t = String(tier).trim();
  var n = String(name).trim();
  if (/^[0-9]+$/.test(t)) t = 'T' + t;   // '7' -> 'T7'; deixa 'T7' e '8.4' como estão
  if (t && n) return t + ' ' + n;
  return n || t;
}

/**
 * Devolve a escalação atual (A3:S): TODAS as linhas com role (B) — inclusive
 * slots VAGOS (name = ''), que o bot usa pra saber as vagas abertas da comp
 * (gate de funções do mass-info).
 */
function getAssignments(req) {
  var pageName = String(req.page_name || '').trim();
  if (!pageName) return { ok: false, error: 'page_name é obrigatório' };
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(pageName);
  if (!sh) return { ok: false, error: 'página do CTA não existe: ' + pageName };

  var firstRow = ASSIGN_FIRST_ROW;
  var lastRow = sh.getLastRow();
  var out = [];
  if (lastRow >= firstRow) {
    var numRows = lastRow - firstRow + 1;
    var vals = sh.getRange(firstRow, 1, numRows, 19).getValues();  // A..S
    for (var i = 0; i < numRows; i++) {
      var role = String(vals[i][1]).trim();      // B
      if (!role) continue;
      var raw = String(vals[i][0]);              // A (pode ter coroa)
      var name = _stripCrown(raw);               // '' = slot VAGO (vaga aberta da comp)
      out.push({
        row:       firstRow + i,
        party:     Math.floor(i / PARTY_SIZE) + 1,
        leader:    raw.indexOf(CROWN) !== -1,
        name:      name,
        role:      role,
        weapon:    _combineTierName(vals[i][2],  vals[i][3]),   // C/D
        offhand:   _combineTierName(vals[i][4],  vals[i][5]),   // E/F
        helmet:    _combineTierName(vals[i][6],  vals[i][7]),   // G/H
        armor:     _combineTierName(vals[i][8],  vals[i][9]),   // I/J
        boots:     _combineTierName(vals[i][10], vals[i][11]),  // K/L
        cape:      _combineTierName(vals[i][12], vals[i][13]),  // M/N
        food:      _combineTierName(vals[i][14], vals[i][15]),  // O/P
        abilities: String(vals[i][16]).trim(),   // Q
        style:     String(vals[i][17]).trim(),   // R
        obs:       String(vals[i][18]).trim()    // S
      });
    }
  }
  return { ok: true, assignments: out };
}


// ===================== HELPERS =====================

function _sheet(name) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh) throw 'página não encontrada: ' + name;
  return sh;
}

/** Devolve a linha (array de células) da comp em COMPS, ou null. */
function _findCompRow(comp) {
  var sh = _sheet(COMPS_SHEET);
  var last = sh.getLastRow();
  if (last <= COMPS_HEADER_ROWS) return null;
  var vals = sh.getRange(COMPS_HEADER_ROWS + 1, 1, last - COMPS_HEADER_ROWS, 3).getValues();
  var needle = comp.toLowerCase();
  for (var i = 0; i < vals.length; i++) {
    if (String(vals[i][0]).trim().toLowerCase() === needle) return vals[i];
  }
  return null;
}

/** Gera um nome de página único. Se já existir, anexa " #eventId" e depois -2, -3... */
function _uniqueSheetName(base, eventId) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  if (!ss.getSheetByName(base)) return base;
  var withId = eventId ? (base + ' #' + eventId) : base;
  if (!ss.getSheetByName(withId)) return withId;
  var n = 2;
  while (ss.getSheetByName(withId + '-' + n)) n++;
  return withId + '-' + n;
}

function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
