import { useT } from "../i18n";

// /register, /addmoney e /removemoney tentam ADIVINHAR a intenção do usuário
// a partir de texto livre (quem é o alvo, qual o valor) em vez de campos
// rígidos — exemplos concretos ajudam mais aqui do que só descrever a regra.
function ExampleList({ items, label }: { items: { code: string; caption: string }[]; label: string }) {
  return (
    <div className="mt-3">
      <p className="text-[11px] text-zinc-600 mb-1.5">{label}</p>
      <div className="space-y-1.5">
        {items.map((ex, i) => (
          <div key={i} className="flex flex-col gap-0.5">
            <code className="text-[11px] font-mono text-amber-300/90 bg-black/30 rounded px-2 py-1 w-fit">{ex.code}</code>
            <span className="text-[11px] text-zinc-600 pl-2">{ex.caption}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function BotDocsPage() {
  const t = useT();
  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-10">
      <h1 className="text-lg font-bold text-zinc-100 mb-1">{t("docsTitle")}</h1>
      <p className="text-sm text-zinc-500 mb-8">{t("docsSubtitle")}</p>

      <div className="space-y-4">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-user-check text-emerald-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/register &lt;{t("docsArgNick")}&gt; [{t("docsArgUser")}]</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-3 leading-relaxed">{t("docsRegisterDesc")}</p>
          <ul className="text-xs text-zinc-500 space-y-2 list-disc pl-4">
            <li>{t("docsRegisterLi1")}</li>
            <li>{t("docsRegisterLi2")}</li>
            <li>{t("docsRegisterLi3")}</li>
            <li>{t("docsRegisterLi4")}</li>
            <li>{t("docsRegisterLi5")}</li>
            <li>{t("docsRegisterLi6")}</li>
            <li>{t("docsRegisterApiNote")}</li>
          </ul>
          <ExampleList label={t("docsExamplesLabel")} items={[
            { code: "/register Kaelen", caption: t("docsRegisterEx1Caption") },
            { code: "/register Kaelen @Rivera", caption: t("docsRegisterEx2Caption") },
          ]} />
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-user-x text-red-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/unregister &lt;{t("docsArgUser")}&gt;</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsUnregisterDesc")}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-photo text-amber-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/avatar e /banner</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsAvatarBannerDesc")}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-settings text-violet-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-semibold text-zinc-100">{t("docsConfigTitle")}</h2>
          </div>
          <ol className="text-xs text-zinc-500 space-y-2 list-decimal pl-4 leading-relaxed">
            <li>{t("docsConfigLi1")}</li>
            <li>{t("docsConfigLi2")}</li>
            <li>{t("docsConfigLi3")}</li>
            <li>{t("docsConfigLi4")}</li>
          </ol>
        </div>

        <p className="text-[10px] font-semibold uppercase tracking-wide text-zinc-600 mt-2">
          {t("cmdCategoryEconomy")}
        </p>
        <p className="text-xs text-zinc-500 leading-relaxed -mt-2 mb-1">{t("docsEconomyIntro")}</p>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-wallet text-blue-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/balance [{t("docsArgUser")}]</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsBalanceDesc")}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-send text-cyan-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/pay &lt;{t("docsArgUser")}&gt; &lt;{t("docsArgAmount")}&gt;</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsPayDesc")}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-coins text-green-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/addmoney &lt;{t("docsArgUser")}&gt; &lt;{t("docsArgAmount")}&gt;</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-3 leading-relaxed">{t("docsAddmoneyDesc")}</p>
          <ul className="text-xs text-zinc-500 space-y-2 list-disc pl-4">
            <li>{t("docsAddmoneyLi1")}</li>
            <li>{t("docsAddmoneyLi2")}</li>
            <li>{t("docsAddmoneyLi3")}</li>
          </ul>
          <ExampleList label={t("docsExamplesLabel")} items={[
            { code: "/addmoney @Rivera 100k", caption: t("docsAddmoneyEx1Caption") },
            { code: "/addmoney @Rivera @Officers 50k", caption: t("docsAddmoneyEx2Caption") },
            { code: "!addmoney @Rivera @Officers 50k", caption: t("docsAddmoneyEx3Caption") },
          ]} />
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-cash-banknote-off text-red-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/removemoney &lt;{t("docsArgUser")}&gt; [{t("docsArgAmount")}]</h2>
          </div>
          <p className="text-xs text-zinc-500 mb-3 leading-relaxed">{t("docsRemovemoneyDesc")}</p>
          <ul className="text-xs text-zinc-500 space-y-2 list-disc pl-4">
            <li>{t("docsRemovemoneyLi1")}</li>
            <li>{t("docsRemovemoneyLi2")}</li>
            <li>{t("docsRemovemoneyLi3")}</li>
            <li>{t("docsRemovemoneyLi4")}</li>
          </ul>
          <ExampleList label={t("docsExamplesLabel")} items={[
            { code: "/removemoney @Rivera 100", caption: t("docsRemovemoneyEx1Caption") },
            { code: "/removemoney @Rivera all", caption: t("docsRemovemoneyEx2Caption") },
            { code: "/removemoney @Officers all", caption: t("docsRemovemoneyEx3Caption") },
          ]} />
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-trophy text-amber-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/leaderboard</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsLeaderboardDesc")}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-chart-bar text-violet-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/economystats</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsEconomystatsDesc")}</p>
        </div>

        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
          <div className="flex items-center gap-3 mb-3">
            <i className="ti ti-arrow-back-up text-orange-400 text-xl" aria-hidden="true" />
            <h2 className="text-sm font-mono font-semibold text-zinc-100">/undo &lt;id&gt;</h2>
          </div>
          <p className="text-xs text-zinc-500 leading-relaxed">{t("docsUndoDesc")}</p>
        </div>
      </div>
    </div>
  );
}
