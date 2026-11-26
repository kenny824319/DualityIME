#include "ja_session.h"

#include "candlayout.h"

namespace sz {

bool JaData::ready() {
    if (!tried) {
        tried = true;
        table = RomajiTable::load(dir);
        dict = JaDict::load(dir);
        if (!table || !dict) {
            table.reset();
            dict.reset();
        } else {
            mixed = std::make_unique<JaMixed>(*table, *dict);
            bigram = JaBigram::load(dir, *dict);  // (none: conversion as without it)
            mixed->bigram = bigram.get();
        }
    }
    return table && dict;
}

bool JaSession::is_japanese(const Json& req) {
    const Json* op = req.get("op");
    std::string o = op && op->is_string() ? op->as_string() : "";
    if (o == "ja_kana" || o == "ja_flush") return true;
    const Json* ja = req.get("ja");
    return o == "key" && ja && ja->is_bool() && ja->as_bool();
}

static Json reply(const JaComposer* c, const Config& config, bool handled, const ustr& commit) {
    Json o = Json::object();
    o["handled"] = Json(handled);
    o["ja"] = Json(true);
    o["commit"] = Json(to_utf8(commit));
    o["composition"] = Json(c ? to_utf8(c->composition) : std::string());
    o["cursor"] = Json(c ? c->cursor : 0);
    Json cands = Json::array();
    int index = 0, size = c ? c->page_size() : 9, count = c ? static_cast<int>(c->candidates.size()) : 0;
    if (c) {
        auto [page, at] = c->page_candidates();
        for (auto& p : page) cands.push(Json(to_utf8(p)));
        index = at;
    }
    o["candidates"] = cands;
    o["cand_index"] = Json(index);
    o["cand_page"] = Json(c ? c->cand_index / size : 0);
    o["cand_pages"] = Json((count + size - 1) / size);
    o["cand_labels"] = Json(cand_keys(config));
    o["cand_per_line"] = Json(cand_per_line(config));
    o["cand_expanded"] = Json(false);
    o["hint"] = Json(std::string());
    o["zhuyin_tip"] = Json(std::string());
    o["zhuyin_tip_at"] = Json(0);
    o["replacements"] = Json::array();
    o["repl_index"] = Json(-1);
    o["symbol_page"] = Json(-1);
    o["symbol_tab"] = Json(-1);
    o["symbol_cursor"] = Json(0);
    o["can_undo"] = Json(false);
    o["kana_lock"] = Json(c ? c->kana_lock : std::string("auto"));
    return o;
}

JaSession::Mixed JaSession::mixed(const Json& req, JaData& data, const Config& config) {
    Mixed m;
    const Json* jm = req.get("ja_mixed");
    if (!jm || !jm->is_bool() || !jm->as_bool()) return m;
    m.on = config.flag("ja_enabled") && data.ready() && data.mixed;  // (the data read only when asked for)
    m.ja_off = !m.on;
    return m;
}

Json JaSession::handle(const Json& req, JaData& data, const Config& config) {
    const Json* opv = req.get("op");
    std::string op = opv && opv->is_string() ? opv->as_string() : "";
    if (!config.flag("ja_enabled") || !data.ready()) {
        // (turned off in the settings, or no data: what was typed goes out, the IME goes back to Chinese)
        ustr out = c_ ? c_->flush() : ustr();
        c_.reset();
        Json o = reply(nullptr, config, false, out);
        o["ja_off"] = Json(true);
        return o;
    }
    if (!c_) c_ = std::make_unique<JaComposer>(*data.table, *data.dict, config, data.bigram.get());
    JaComposer& c = *c_;
    bool handled = true;
    ustr commit;
    if (op == "key") {
        const Json* chv = req.get("ch");
        const Json* sp = req.get("special");
        std::optional<char32_t> ch;
        if (chv && chv->is_string()) {
            ustr u = from_utf8(chv->as_string());
            if (u.size() == 1) ch = u[0];
        }
        std::string special = sp && sp->is_string() ? sp->as_string() : "";
        handled = special.empty() && !ch ? false : c.key(ch, special);
        commit = c.commit;
    } else if (op == "ja_kana") {
        const Json* lock = req.get("lock");
        c.set_kana_lock(lock && lock->is_string() ? lock->as_string() : "auto");
    } else if (op == "ja_flush") {  // (switching to Chinese: what is typed is sent, the kana lock back to 自動)
        commit = c.flush();  // (the list closed first: what the screen shows goes - findings #163)
        const Json* keep = req.get("keep_lock");  // (Shift to 中日混打: the lock stays, both modes' Japanese hold it)
        if (!(keep && keep->is_bool() && keep->as_bool())) c.set_kana_lock("auto");
    }
    c.commit.clear();  // sent once
    return reply(&c, config, handled, commit);
}

}  // namespace sz
