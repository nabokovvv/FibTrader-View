/** Donation wallets (public instance). Network names are shown exactly; sending on another network loses funds. */
export interface Wallet { coin: string; network: string; networkHint: string; address: string }

export const WALLETS: Wallet[] = [
  { coin: 'USDT', network: 'TRC20', networkHint: 'TRON', address: 'TRzKHcyrzzjdqdaojgVYEWLxxSi5nXxJg7' },
  { coin: 'USDT', network: 'BEP20', networkHint: 'BNB Smart Chain', address: '0xe96e43569491FCcE4f7a9f47B4c7B0503E0438b4' },
]
