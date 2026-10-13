"""Plain-text recording display; shared by live and workflow health publishers."""

def markdown(data):
    health = data.get('monitoring')
    unknown = (bool(health and health['needsAttention']
                    and not any(r['raceDate'] == health['raceDate'] for r in data['races']))
               or (not data['races'] and bool(data.get('warnings'))))
    target = f"記録一覧の対象 {data['counts']['totalRaces']}件"
    lines = ['# 研究記録状況', '', f"更新：{data['generatedAtJST']} JST", '',
             f"{target}／予測保存済み {data['counts'].get('saved', 0)}件／要確認のレース {data['counts'].get('needsAttention', 0)}件", '',
             'ペース予想の表示は保留中です。この一覧は研究用データの保存と照合の状況です。', '']
    if unknown:
        lines += ['監視日の対象件数は未確認です。保存済みの記録一覧の件数とは区別してください。', '']
    if health:
        lines += [f"自動監視：{health['label']}（{health['raceDate']}）", '', health['message'], '',
                  f"[詳しい実行状況]({health['detailsURL']})", '']
    warnings = [w for w in data.get('warnings', []) if w not in data.get('monitorWarnings', [])]
    if warnings:
        lines += warnings + ['']
    if not data['races']:
        return '\n'.join(lines + [data['emptyMessage'], ''])
    lines += ['| 日付 | レース | 発走 | 記録 | 結果照合 | 原因・確認事項 |',
              '|---|---|---|---|---|---|']
    for row in data['races']:
        reason = ' '.join([row['reason']] + row['issues']).replace('|', '／').replace('\n', ' ')
        lines.append(f"| {row['raceDate']} | {row['venue']}{row['raceNumber']}R | {row['startTime'] or '未収録'} | {row['recordingLabel']} | {row['resultLabel']} | {reason} |")
    return '\n'.join(lines) + '\n'
