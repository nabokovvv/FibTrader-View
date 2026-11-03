import { Check, Copy } from 'lucide-react'
import qrcode from 'qrcode-generator'
import { useMemo, useState } from 'react'
import { WALLETS, type Wallet } from '../config/donate'
import { t } from '../i18n'
import { Modal } from './ui'

/** "Support the project": wallet addresses with QR codes. No payment processing, nothing sent to the server. */
export function DonateModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title={t('Support the project')} onClose={onClose} width={560}>
      <div className="flex flex-col gap-4 p-4">
        <p className="text-[13px] leading-relaxed text-muted">
          {t('FibTrader View is free. If it helps you, you can chip in for the servers and the AI tokens that build it 🙂')}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {WALLETS.map((w) => <WalletCard key={w.address} w={w} />)}
        </div>
        <p className="text-[11px] text-faint">{t('Send only USDT on the network shown on the card. Coins sent on another network are lost.')}</p>
      </div>
    </Modal>
  )
}

function WalletCard({ w }: { w: Wallet }) {
  const [copied, setCopied] = useState(false)
  const qr = useMemo(() => qrSvgPath(w.address), [w.address])
  const copy = () => {
    void navigator.clipboard?.writeText(w.address).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 2000) })
  }
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-line p-3">
      <div className="text-center">
        <div className="text-[14px] font-semibold">{w.coin} · {w.network}</div>
        <div className="text-[11px] text-muted">{w.networkHint}</div>
      </div>
      <svg viewBox={`0 0 ${qr.size} ${qr.size}`} className="h-[150px] w-[150px] rounded-md bg-white p-2" shapeRendering="crispEdges" role="img" aria-label={t('QR code of the address')}>
        <path d={qr.path} fill="#111" />
      </svg>
      <button className="flex w-full items-center gap-2 rounded-md border border-line px-2 py-1.5 text-left hover:bg-raised" onClick={copy} title={t('Copy address')}>
        <span className="min-w-0 flex-1 break-all font-mono text-[11px]">{w.address}</span>
        {copied ? <Check className="h-4 w-4 shrink-0" style={{ color: 'var(--up)' }} /> : <Copy className="h-4 w-4 shrink-0 text-muted" />}
      </button>
      {copied && <span className="text-[11px]" style={{ color: 'var(--up)' }}>{t('Address copied')}</span>}
    </div>
  )
}

function qrSvgPath(text: string): { size: number; path: string } {
  const q = qrcode(0, 'M')
  q.addData(text)
  q.make()
  const n = q.getModuleCount()
  let path = ''
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) path += `M${c} ${r}h1v1h-1z`
  return { size: n, path }
}
