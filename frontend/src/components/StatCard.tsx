import React from 'react';
import { Card } from './ui';

interface StatCardProps { title: string; value: string | number; icon: React.ReactNode; change?: string; changePositive?: boolean; }

export function StatCard({ title, value, icon, change, changePositive }: StatCardProps) {
  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm text-slate-500 mb-1">{title}</p>
          <p className="text-2xl font-bold text-white">{value}</p>
          {change && <p className={`text-xs mt-1 ${changePositive ? 'text-green-400' : 'text-red-400'}`}>{change}</p>}
        </div>
        <div className="p-2.5 rounded-lg" style={{ background: 'rgba(37,99,235,0.15)', color: '#60a5fa' }}>{icon}</div>
      </div>
    </Card>
  );
}
