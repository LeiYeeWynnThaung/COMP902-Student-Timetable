"""COMP902 scheduling core: independent of Streamlit, deterministic and testable."""
from __future__ import annotations
import math
import re
from datetime import datetime
from typing import Any
import pandas as pd

DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
WEIGHTS = {'High':3,'Medium':2,'Low':1}
STEP=30
WEEK=7*1440

def minutes(value: Any) -> int:
    """Parse strict HH:MM, HH:MM:SS, or h:mm AM/PM. Reject ambiguous input."""
    if isinstance(value,(int,float)) and not pd.isna(value):
        if 0 <= value < 1440 and int(value)==value: return int(value)
        raise ValueError(f'Invalid time: {value}')
    s=str(value).strip().upper().replace('.', '')
    for fmt in ('%H:%M','%H:%M:%S','%I:%M %p','%I:%M:%S %p'):
        try:
            dt=datetime.strptime(s,fmt)
            return dt.hour*60+dt.minute
        except ValueError: pass
    raise ValueError(f'Invalid time {value!r}. Use 15:30 or 3:30 PM.')

def hhmm(n):
    n=int(n)%1440
    return f'{n//60:02d}:{n%60:02d}'

def daylist(v):
    if isinstance(v,list): return [x for x in DAYS if x in v]
    if v is None:return []
    return [x for x in DAYS if re.search(r'\b'+x+r'\b',str(v),re.I)]

def default_profile():
    """Return a genuinely empty student profile (no sample data)."""
    return dict(student_id='NEW001', age_group='', year_level='',
        school=[dict(day=d, enabled=False, start='', end='', travel_before=0,
                     travel_after=0) for d in DAYS],
        sleep=[dict(day=d, bedtime='', wake='') for d in DAYS],
        fixed=[], flexible=[], unavailable=[], notes='', screen_free=[])


def _block(grid, exact, conflicts, label, start, end, category):
    """Reserve every touched 30-min slot, but record true-minute overlaps separately."""
    if end<=start:raise ValueError(f'{label}: end must be after start')
    if end-start>1440:raise ValueError(f'{label}: duration exceeds 24 hours')
    for s,e,old,cat in exact:
        # exact entries may be from previous week boundary
        if max(s,start)<min(e,end):
            conflicts.append(dict(existing=old,new=label,start=hhmm(max(s,start)),end=hhmm(min(e,end)),minutes=min(e,end)-max(s,start),category=f'{cat} / {category}'))
    exact.append((start,end,label,category))
    for k in range(start//STEP, (end+STEP-1)//STEP):
        idx=k%336
        if not grid[idx]:grid[idx]=dict(label=label,category=category,priority='')
        # overlapping grid cells may be conservative 30-min rounding, not true overlap

def validate(profile):
    """Return errors, warnings. Errors block scheduling; warnings do not silently mutate input."""
    errors=[];warnings=[]
    def error(msg):errors.append(msg)
    def warn(msg):warnings.append(msg)
    if not str(profile.get('student_id','')).strip():error('Student ID is required.')
    for r in profile.get('school',[]):
        if not r.get('enabled'):continue
        try:
            a,b=minutes(r['start']),minutes(r['end'])
            if b<=a:error(f"School {r['day']}: finish must be after start (same day).")
            elif not 120<=b-a<=600:warn(f"School {r['day']}: unusual school duration ({b-a} minutes). Please verify.")
            if b<720:warn(f"School {r['day']}: morning finish time {hhmm(b)} looks suspicious; check AM/PM.")
        except (ValueError,KeyError) as exc:error(f"School {r.get('day','?')}: {exc}")
        for key in ('travel_before','travel_after'):
            if not isinstance(r.get(key,0),(int,float)) or r.get(key,0)<0:error(f"School {r.get('day')}: invalid {key}.")
    for r in profile.get('sleep',[]):
        try:
            b,w=minutes(r['bedtime']),minutes(r['wake'])
            dur=(w-b)%1440
            if not 240<=dur<=900:warn(f"Sleep {r['day']}: {dur} minutes; verify bedtime and wake time.")
            if 360<=b<1080:warn(f"Sleep {r['day']}: bedtime {hhmm(b)} is unusual (possible AM/PM mistake).")
        except (ValueError,KeyError) as exc:error(f"Sleep {r.get('day','?')}: {exc}")
    for r in profile.get('fixed',[]):
        if not str(r.get('name','')).strip():error('A fixed activity has no name.');continue
        if not daylist(r.get('days')):error(f"Fixed {r['name']}: select at least one day.")
        try:
            a,b=minutes(r['start']),minutes(r['end']);duration=(b-a)%1440
            if duration==0 or duration>720:error(f"Fixed {r['name']}: invalid or excessive duration.")
        except (ValueError,KeyError) as exc:error(f"Fixed {r['name']}: {exc}")
        for key in ('travel_before','travel_after'):
            if not isinstance(r.get(key,0),(int,float)) or r.get(key,0)<0:error(f"Fixed {r['name']}: invalid travel time.")
    for r in profile.get('flexible',[]):
        if not str(r.get('name','')).strip():error('A flexible activity has no name.');continue
        if r.get('priority') not in (*WEIGHTS,'Not applicable'):error(f"{r['name']}: choose High, Medium, Low or Not applicable.")
        if r.get('priority')=='Not applicable':continue
        if not daylist(r.get('days')):error(f"{r['name']}: select eligible days.")
        for k in ('requested_minutes','min_session_minutes'):
            v=r.get(k)
            if not isinstance(v,(int,float)) or v<=0:error(f"{r['name']}: {k} must be positive.")
        try:
            a,b=minutes(r['window_start']),minutes(r['window_end'])
            if b<=a:error(f"{r['name']}: eligible window must end after it starts on the same day.")
        except (ValueError,KeyError) as exc:error(f"{r['name']}: {exc}")
    for r in profile.get('flexible', []):
        if r.get('priority') not in WEIGHTS:
            continue
        mode = r.get('distribution_mode', 'Algorithm decides')
        if mode not in ('Equal across selected days', 'Custom minutes per day', 'Algorithm decides'):
            error(f"{r.get('name','Activity')}: invalid distribution mode.")
            continue
        if mode != 'Algorithm decides':
            selected_days = daylist(r.get('days'))
            daily = r.get('daily_minutes', {})
            if not selected_days:
                continue
            if mode == 'Equal across selected days':
                total = int(r.get('requested_minutes', 0))
                if total % 15:
                    error(f"{r['name']}: weekly duration must be divisible by 15 minutes.")
            else:
                if any(not isinstance(daily.get(d), (int,float)) or daily[d] < 0 or daily[d] % 15 for d in selected_days):
                    error(f"{r['name']}: each selected day's duration must be a nonnegative multiple of 15 minutes.")
                elif sum(daily[d] for d in selected_days) != r['requested_minutes']:
                    error(f"{r['name']}: daily targets must add up to weekly requested minutes.")
    for r in profile.get('unavailable',[])+profile.get('screen_free',[]):
        try:
            if minutes(r['end'])<=minutes(r['start']):error(f"Blocked window {r.get('day')}: end must follow start.")
        except (ValueError,KeyError) as exc:error(f'Blocked window: {exc}')
    return errors,warnings

def build(profile):
    grid=[None]*336;exact=[];conflicts=[]
    # Reserve sleep by evening bedtime on each named day until next morning.
    for r in profile.get('sleep',[]):
        di=DAYS.index(r['day']);b=minutes(r['bedtime']);w=minutes(r['wake'])
        if b<720: # morning bedtime is not auto-corrected; validation warns
            start=di*1440+b
        else:start=di*1440+b
        end=start+(w-b)%1440
        if end>start:_block(grid,exact,conflicts,'Sleep',start,end,'Sleep')
    for r in profile.get('school',[]):
        if not r.get('enabled'):continue
        di=DAYS.index(r['day']);a=di*1440+minutes(r['start']);b=di*1440+minutes(r['end'])
        _block(grid,exact,conflicts,'School',a,b,'School')
        if r.get('travel_before',0):_block(grid,exact,conflicts,'Travel to school',a-int(r['travel_before']),a,'Travel')
        if r.get('travel_after',0):_block(grid,exact,conflicts,'Travel from school',b,b+int(r['travel_after']),'Travel')
    for r in profile.get('fixed',[]):
        for day in daylist(r.get('days')):
            di=DAYS.index(day);a=di*1440+minutes(r['start']);end=minutes(r['end']);b=di*1440+end
            if b<=a:b+=1440
            _block(grid,exact,conflicts,r['name'],a,b,'Fixed')
            if str(r.get('mode','')).lower()!='online':
                if r.get('travel_before',0):_block(grid,exact,conflicts,'Travel to '+r['name'],a-int(r['travel_before']),a,'Travel')
                if r.get('travel_after',0):_block(grid,exact,conflicts,'Travel from '+r['name'],b,b+int(r['travel_after']),'Travel')
    for r in profile.get('unavailable',[]):
        di=DAYS.index(r['day']);a=di*1440+minutes(r['start']);b=di*1440+minutes(r['end'])
        _block(grid,exact,conflicts,'Unavailable',a,b,'Unavailable')
    for r in profile.get('screen_free',[]):
        # screen-free periods are only activity-specific exclusions, not general blocks
        pass
    return grid,conflicts

def _eligible(r,profile):
    a,b=minutes(r['window_start']),minutes(r['window_end'])
    slots=[]
    for d in daylist(r.get('days')):
        di=DAYS.index(d)
        for k in range(math.ceil(a/STEP),math.floor(b/STEP)):
            if k<48:slots.append(di*48+k)
    if any(w in r['name'].lower() for w in ('gaming','screen','video','tv','social media')):
        for blk in profile.get('screen_free',[]):
            if blk['day'] in daylist(r.get('days')):
                di=DAYS.index(blk['day']);s=minutes(blk['start']);e=minutes(blk['end'])
                slots=[k for k in slots if not (di*48+math.floor(s/30)<=k<di*48+math.ceil(e/30))]
    return set(slots)

def run(profile,algorithm='priority'):
    errors,warnings=validate(profile)
    if errors:raise ValueError('\n'.join(errors))
    grid,conflicts=build(profile)
    tasks=[(i,r) for i,r in enumerate(profile.get('flexible',[])) if r.get('priority') in WEIGHTS]
    if algorithm=='priority':tasks.sort(key=lambda ir:(-WEIGHTS[ir[1]['priority']],ir[0]))
    allocations=[]
    daily_rows=[]
    for i,r in tasks:
        req=int(r['requested_minutes'])
        minimum=math.ceil(int(r['min_session_minutes'])/STEP)
        eligible=_eligible(r,profile)
        sessions=[]
        occupied=0
        mode=r.get('distribution_mode','Algorithm decides')
        selected_days=daylist(r.get('days'))
        if mode == 'Equal across selected days' and selected_days:
            units,extra=divmod(req//15,len(selected_days))
            daily={d:(units+(j<extra))*15 for j,d in enumerate(selected_days)}
        elif mode == 'Custom minutes per day':
            daily={d:int(r.get('daily_minutes',{}).get(d,0)) for d in selected_days}
        else:
            daily=None

        # Daily targets are hard constraints; do not transfer unmet minutes to other days.
        targets=[(d,daily[d]) for d in selected_days] if daily is not None else [(None,req)]
        scheduled_total=0
        daily_breakdown=[]
        for day,target in targets:
            need=math.ceil(target/STEP)
            count=0
            day_eligible={k for k in eligible if day is None or k//48==DAYS.index(day)}
            while count<need:
                runs=[];begin=None
                for k in range(337):
                    available=k<336 and k in day_eligible and grid[k] is None
                    if available and begin is None:begin=k
                    if not available and begin is not None:runs.append((begin,k));begin=None
                if algorithm=='priority':runs.sort(key=lambda pair:(-(pair[1]-pair[0]),pair[0]))
                else:runs.sort()
                choice=next(((a,b) for a,b in runs if min(b-a,need-count)>=minimum),None)
                if choice is None:break
                a,b=choice;take=min(b-a,need-count)
                for k in range(a,a+take):
                    grid[k]=dict(label=r['name'],category='Flexible',priority=r['priority'])
                sessions.append((a,take));count+=take
            actual=min(target,count*STEP)
            scheduled_total+=actual
            occupied+=count*STEP
            if day is not None:
                daily_rows.append(dict(activity=r['name'],priority=r['priority'],day=day,requested_minutes=target,scheduled_minutes=actual,unmet_minutes=target-actual))
        allocations.append(dict(activity_id=i+1,activity=r['name'],priority=r['priority'],
            distribution_mode=mode,requested_minutes=req,scheduled_minutes=scheduled_total,
            unmet_minutes=req-scheduled_total,occupied_minutes=occupied,sessions=len(sessions),
            ))
    alloc=pd.DataFrame(allocations,columns=['activity_id','activity','priority','requested_minutes','scheduled_minutes','unmet_minutes','occupied_minutes','sessions','distribution_mode'])
    requested=int(alloc.requested_minutes.sum()) if len(alloc) else 0
    scheduled=int(alloc.scheduled_minutes.sum()) if len(alloc) else 0
    if len(alloc):
        weights=alloc.priority.map(WEIGHTS)
        wreq=int((alloc.requested_minutes*weights).sum());wsch=int((alloc.scheduled_minutes*weights).sum())
    else:wreq=wsch=0
    metrics=dict(algorithm=algorithm,activity_satisfaction_pct=round(scheduled/requested*100,2) if requested else None,
                 priority_satisfaction_pct=round(wsch/wreq*100,2) if wreq else None,
                 high_priority_satisfaction_pct=round(100*alloc.loc[alloc.priority=='High','scheduled_minutes'].sum()/alloc.loc[alloc.priority=='High','requested_minutes'].sum(),2) if len(alloc) and alloc.loc[alloc.priority=='High','requested_minutes'].sum() else None,
                 requested_minutes=requested,scheduled_minutes=scheduled,unmet_minutes=requested-scheduled,
                 unmet_activities=int((alloc.unmet_minutes>0).sum()) if len(alloc) else 0,fixed_conflicts=len(conflicts))
    rows=[]
    for k,v in enumerate(grid):
        rows.append(dict(day=DAYS[k//48],start=hhmm((k%48)*30),end=hhmm(((k%48)*30+30)%1440),activity=v['label'] if v else '',category=v['category'] if v else 'Free',priority=v['priority'] if v else ''))
    return dict(metrics=metrics,allocations=alloc,daily_allocations=pd.DataFrame(daily_rows,columns=['activity','priority','day','requested_minutes','scheduled_minutes','unmet_minutes']),timetable=pd.DataFrame(rows),conflicts=pd.DataFrame(conflicts,columns=['existing','new','start','end','minutes','category']),warnings=warnings)

def compare(profile):
    return run(profile,'baseline'),run(profile,'priority')

def import_pipeline_folder(folder):
    """Read existing COMP902_Complete_Outputs CSVs; return profiles and import warnings.
    Some questionnaire window labels are approximated; review before evaluation.
    """
    from pathlib import Path
    p=Path(folder)
    required=['profiles.csv','school_cleaned.csv','sleep.csv','fixed_activities.csv','flexible_activities.csv']
    missing=[x for x in required if not (p/x).exists()]
    if missing:raise FileNotFoundError('Missing pipeline exports: '+', '.join(missing))
    frames={x[:-4]:pd.read_csv(p/x).fillna('') for x in required}
    warnings=[];profiles={}
    for _,r in frames['profiles'].iterrows():
        q=default_profile();sid=str(r.student_id)
        q.update(student_id=sid,age_group=str(r.get('age_group','')),year_level=str(r.get('year_level','')),
                 school=[dict(day=d,enabled=False,start='08:45',end='15:00',travel_before=0,travel_after=0) for d in DAYS],sleep=[],fixed=[],flexible=[],unavailable=[],screen_free=[],notes='Imported from research pipeline; review time windows and validation warnings.')
        profiles[sid]=q
    for _,r in frames['school_cleaned'].iterrows():
        sid=str(r.student_id)
        if sid not in profiles or r.day not in DAYS:continue
        if str(r.valid).lower() not in ('true','1'):warnings.append(f'{sid} {r.day}: invalid school record skipped');continue
        if r.start=='' or r.end=='':continue
        school=profiles[sid]['school'][DAYS.index(r.day)]
        school.update(enabled=True,start=hhmm(int(float(r.start))),end=hhmm(int(float(r.end))),travel_before=int(float(r.one_way_travel or 0)),travel_after=int(float(r.one_way_travel or 0)))
    for _,r in frames['sleep'].iterrows():
        sid=str(r.student_id)
        if sid not in profiles or r.day not in DAYS:continue
        profiles[sid]['sleep'].append(dict(day=r.day,bedtime=hhmm(int(float(r.start))),wake=hhmm(int(float(r.end)))))
    for _,r in frames['fixed_activities'].iterrows():
        sid=str(r.student_id)
        if sid not in profiles or r.day not in DAYS:continue
        if str(r.get('active','True')).lower() not in ('true','1'):continue
        profiles[sid]['fixed'].append(dict(name=str(r.activity),days=[r.day],start=hhmm(int(float(r.start))),end=hhmm(int(float(r.end))),travel_before=int(float(r.travel_before or 0)),travel_after=int(float(r.travel_after or 0)),mode=str(r.get('mode','In person'))))
    def window_parse(s):
        z=str(s).lower()
        pairs=re.findall(r'(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})',z)
        if pairs:
            a,b,c,d=pairs[0];return f'{int(a):02d}:{b}',f'{int(c):02d}:{d}'
        if 'after school' in z:return '15:30','18:00'
        if 'morning' in z:return '09:00','12:00'
        if 'afternoon' in z:return '12:00','17:00'
        if 'evening' in z:return '17:00','21:00'
        return '16:00','20:00'
    for _,r in frames['flexible_activities'].iterrows():
        sid=str(r.student_id)
        if sid not in profiles:continue
        pri=str(r.priority)
        if pri not in WEIGHTS:continue
        win=window_parse(r.windows)
        if ',' in str(r.windows):warnings.append(f'{sid} {r.activity}: multiple eligible windows approximated by the first; review before research use')
        profiles[sid]['flexible'].append(dict(name=str(r.activity),priority=pri,requested_minutes=int(float(r.requested_min)),min_session_minutes=max(1,int(float(r.minimum_session_min))),days=daylist(r.days),window_start=win[0],window_end=win[1]))
    for sid,q in profiles.items():
        if len(q['sleep'])!=7:warnings.append(f'{sid}: only {len(q["sleep"])} sleep records; review before scheduling')
    return profiles,warnings
