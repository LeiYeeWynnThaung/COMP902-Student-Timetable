"""Run: streamlit run app.py"""
import io
import json
import html
from datetime import datetime
import zipfile
import secrets
from pathlib import Path
import pandas as pd
import streamlit as st
from scheduler import DAYS, WEIGHTS, default_profile, minutes, hhmm, compare, validate, import_pipeline_folder
from storage import load_profiles, save_profile, delete_profile, save_results, DB_PATH, new_profile_credentials, load_profile_by_credentials, save_secured_profile, profile_count, MAX_SAVED_PROFILES, load_legacy_profile

def blank_profile(student_id='NEW001'):
    q = default_profile()
    q.update(student_id=student_id, age_group='', year_level='', fixed=[], flexible=[], unavailable=[], screen_free=[], notes='')
    q['school'] = [dict(day=d, enabled=False, start='', end='', travel_before=0, travel_after=0) for d in DAYS]
    q['sleep'] = [dict(day=d, bedtime='', wake='') for d in DAYS]
    return q

def next_step(label):
    st.info(f'➡️ **Next step:** Click the **{label}** tab above to continue.')



# Display 12-hour AM/PM choices while retaining HH:MM for the scheduler.
TIME_OPTIONS = ['Select time'] + [
    f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    for h in range(24) for m in (0, 15, 30, 45)
]

def _time_label(value):
    if value is None or str(value).strip() == '':
        return 'Select time'
    try:
        n = minutes(value)
    except (ValueError, TypeError):
        return 'Select time'
    h, m = divmod(n, 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"

def time_picker(label, value, key):
    """15-minute time dropdown; return HH:MM or blank for unselected values."""
    current = _time_label(value)
    # Imported custom times are preserved as a selectable value, not rounded silently.
    choices = TIME_OPTIONS.copy()
    if current not in choices:
        choices.insert(1, current)
    chosen = st.selectbox(label, choices, index=choices.index(current), key=key)
    return '' if chosen == 'Select time' else hhmm(minutes(chosen))


st.set_page_config(page_title='COMP902 • Weekly Timetable Generator',page_icon='📅',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
.block-container{padding-top:1.5rem;max-width:1450px}
.smallnote{color:#64748b;font-size:.88rem}
.legend{display:inline-block;padding:4px 9px;margin:2px;border-radius:5px;font-size:.82rem}
.calendar{width:100%;border-collapse:collapse;table-layout:fixed;font-size:9px}
.calendar th{background:#17263d;color:white;padding:4px;border:1px solid #cbd5e1}
.calendar td{border:1px solid #e2e8f0;height:12px;padding:1px 2px;line-height:1.05;vertical-align:middle;overflow:hidden;word-wrap:break-word}
.calendar .time{background:#f1f5f9;font-weight:600;width:65px;color:#334155}
.calendar .block-time{font-size:8px;opacity:.85;margin-top:1px}.calendar td[rowspan]{vertical-align:middle;text-align:center}.calendar .free{background:#fff}.calendar .School{background:#bfdbfe;color:#1e3a8a}
.calendar .Sleep{background:#ddd6fe;color:#4c1d95}.calendar .Travel{background:#fde68a;color:#78350f}
.calendar .Fixed{background:#fed7aa;color:#7c2d12}.calendar .Unavailable{background:#e5e7eb;color:#374151}
.calendar .High{background:#fecaca;color:#7f1d1d}.calendar .Medium{background:#fef08a;color:#713f12}.calendar .Low{background:#bbf7d0;color:#14532d}
</style>''',unsafe_allow_html=True)

# Always begin a new Streamlit session with a blank profile.
# Previously saved SQLite profiles are loaded ONLY when the parent clicks
# 'Load saved profiles from SQLite' in the sidebar.
if 'profiles' not in st.session_state:
    st.session_state.profiles = {'NEW001': blank_profile('NEW001')}
    st.session_state.selected = 'NEW001'
if 'selected' not in st.session_state:st.session_state.selected='NEW001'
if 'import_warnings' not in st.session_state:st.session_state.import_warnings=[]

st.title('📅 Personalised Weekly Timetable Generator')
st.caption('COMP902 research prototype · Rule-based constraints · Baseline vs Priority-Driven · 30-minute scheduling grid')
st.info('**Start here:** Complete the tabs from left to right: **1 Profile → 2 School → 3 Sleep → 4 Fixed + travel → 5 Flexible activities → 6 Unavailable → 7 Generate & compare**. Fixed and flexible activities are optional; add only those that apply. Click **Save selected profile to SQLite** when finished.')

with st.sidebar:
    st.header('Student & data')
    ids=list(st.session_state.profiles)
    current=st.session_state.selected if st.session_state.selected in ids else ids[0]
    selected=st.selectbox('Choose student profile',ids,index=ids.index(current))
    st.session_state.selected=selected
    if st.button('🧹 Start fresh with blank profile', use_container_width=True,
                 help='Clear the current on-screen profiles and generated results. Does not delete SQLite records.'):
        # Delete only this app's widget/form keys; do not touch saved SQLite data.
        for key in list(st.session_state.keys()):
            if key not in ('profiles', 'selected', 'import_warnings'):
                del st.session_state[key]
        st.session_state.profiles = {'NEW001': blank_profile('NEW001')}
        st.session_state.selected = 'NEW001'
        st.session_state.import_warnings = []
        st.rerun()
    st.markdown('#### Create a private student profile')
    if st.button('➕ Create new student with random ID', use_container_width=True):
        sid, pin = new_profile_credentials()
        # Clear form widget state before switching profiles.
        for key in list(st.session_state.keys()):
            if key not in ('profiles','selected','import_warnings'):
                del st.session_state[key]
        st.session_state.profiles = {sid: blank_profile(sid)}
        st.session_state.selected = sid
        st.session_state[f'pin_for_{sid}'] = pin
        st.session_state['new_credentials'] = (sid, pin)
        st.rerun()
    if st.session_state.get('new_credentials'):
        new_sid, new_pin = st.session_state['new_credentials']
        if new_sid == st.session_state.selected:
            st.success('New profile created. Save this ID and PIN privately.')
            st.code(f'Profile ID: {new_sid}\nPrivate PIN: {new_pin}')
            st.warning('The PIN cannot be recovered if lost. Keep it somewhere private.')
    st.markdown('#### Save this student')
    if st.button('💾 Save current profile', use_container_width=True):
        current_profile = st.session_state.profiles[st.session_state.selected]
        errors, warnings = validate(current_profile)
        if errors or not current_profile.get('age_group') or not current_profile.get('year_level'):
            st.error('Complete Profile, School and Sleep before saving. ' + '; '.join(errors))
        elif str(current_profile['student_id']).startswith('STU-'):
            pin = st.session_state.get(f"pin_for_{current_profile['student_id']}")
            if not pin:
                st.error('This profile needs its private PIN. Load it with the ID and PIN first.')
            else:
                try:
                    outcome = save_secured_profile(current_profile, pin)
                    st.success('Profile saved. Keep your Profile ID and PIN for next time.')
                    if outcome['pruned']:
                        st.warning('Oldest secured profile(s) archived and removed: ' + ', '.join(outcome['pruned']))
                    if warnings: st.warning('Saved with validation warnings; please review.')
                except Exception as exc: st.error(str(exc))
        else:
            try:
                save_profile(current_profile)
                st.success('Legacy research profile saved locally.')
            except Exception as exc: st.error(str(exc))
    st.markdown('#### Return to a saved profile')
    entered_id = st.text_input('Profile ID', placeholder='STU-XXXXXXXXXX', key='lookup_profile_id').strip().upper()
    entered_pin = st.text_input('Private six-digit PIN', type='password', max_chars=6, key='lookup_profile_pin')
    if st.button('📂 Load my saved profile', use_container_width=True):
        try:
            loaded = load_profile_by_credentials(entered_id, entered_pin)
            for key in list(st.session_state.keys()):
                if key not in ('profiles','selected','import_warnings'):
                    del st.session_state[key]
            st.session_state.profiles = {entered_id: loaded}
            st.session_state.selected = entered_id
            st.session_state[f'pin_for_{entered_id}'] = entered_pin
            st.session_state['loaded_notice'] = True
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if st.session_state.pop('loaded_notice', False):
        st.success('Saved profile loaded successfully. You can edit and save it again.')
    st.caption(f'Saved profiles: {profile_count()} / {MAX_SAVED_PROFILES if MAX_SAVED_PROFILES else "unlimited"}. Oldest secured profiles are backed up before automatic removal; legacy records are protected.')
    with st.expander('Local research: open existing legacy profile'):
        st.warning('For local researcher use only. Do not expose this control on a public parent-facing deployment.')
        legacy_id = st.text_input('Legacy student ID', value='NEW001', key='legacy_id')
        if st.button('Load legacy record (local only)'):
            loaded = load_legacy_profile(legacy_id)
            if loaded:
                for key in list(st.session_state.keys()):
                    if key not in ('profiles','selected','import_warnings'):
                        del st.session_state[key]
                st.session_state.profiles = {legacy_id: loaded}
                st.session_state.selected = legacy_id
                st.rerun()
            else: st.error('No matching legacy record found.')
    st.caption('Database: ' + str(DB_PATH) + '. A new session always starts blank; saved profiles load only on request.')
    st.divider()
    st.subheader('Load saved profiles')
    uploaded=st.file_uploader('Import student JSON',type=['json'],help='Upload JSON exported by this prototype')
    if uploaded is not None and st.button('Import JSON'):
        try:
            payload=json.load(uploaded)
            if isinstance(payload,dict) and 'school' in payload:payload={payload['student_id']:payload}
            if not isinstance(payload,dict):raise ValueError('Expected a profile or profile dictionary')
            for sid,p in payload.items():
                if not isinstance(p,dict) or 'school' not in p or 'sleep' not in p:raise ValueError('Invalid profile structure')
                st.session_state.profiles[str(sid)]=p
            st.session_state.selected=next(iter(payload));st.rerun()
        except Exception as exc:st.error(f'Could not import JSON: {exc}')
    with st.expander('Import cleaned COMP902 pipeline outputs'):
        folder=st.text_input('Folder containing profiles.csv, school_cleaned.csv, sleep.csv, fixed_activities.csv, flexible_activities.csv',placeholder=r'C:\COMP902\COMP902_Complete_Outputs')
        if st.button('Load research profiles'):
            try:
                prof,warnings=import_pipeline_folder(folder)
                st.session_state.profiles=prof;st.session_state.import_warnings=warnings
                st.session_state.selected=next(iter(prof));st.rerun()
            except Exception as exc:st.error(str(exc))
        st.caption('Import is a starting point: multiple flexible time windows may need review. This prototype does not replace the research preprocessing notebook.')
    st.download_button('⬇️ Export current profile (JSON)',json.dumps(p if 'p' in locals() else st.session_state.profiles[st.session_state.selected],indent=2,ensure_ascii=False),file_name='COMP902_profile.json',mime='application/json',use_container_width=True)
    st.divider()
    st.caption('Choose times using AM/PM dropdowns in 15-minute increments. Internally, times are stored as HH:MM for scheduling.')

p=st.session_state.profiles[selected]
if st.session_state.import_warnings:
    with st.expander(f'⚠️ {len(st.session_state.import_warnings)} import notices'):
        for msg in st.session_state.import_warnings[:100]:st.write('• '+msg)

profile_tab,school_tab,sleep_tab,fixed_tab,flex_tab,blocked_tab,results_tab=st.tabs(['1 👤 Profile','2 🏫 School','3 🌙 Sleep','4 📌 Fixed + travel','5 ⭐ Flexible activities','6 🚫 Unavailable','7 📊 Generate & compare'])

with profile_tab:
    c1,c2,c3=st.columns(3)
    age_options = ['5–7', '8–10', '11–13', '14–18']
    year_options = [f'Year {i}' for i in range(1, 14)]
    # Keep older imported profile values selectable rather than changing them silently.
    current_age = str(p.get('age_group', '')).strip()
    current_year = str(p.get('year_level', '')).strip()
    if current_age and current_age not in age_options:
        age_options.append(current_age)
    if current_year and current_year not in year_options:
        year_options.append(current_year)
    p['age_group'] = c1.selectbox('Age group', age_options,
        index=age_options.index(current_age) if current_age in age_options else None,
        placeholder='Select age group',
        key=f'age_{selected}')
    p['year_level'] = c2.selectbox('School year', year_options,
        index=year_options.index(current_year) if current_year in year_options else None,
        placeholder='Select school year',
        key=f'year_{selected}')
    new_id=c3.text_input('Student ID (pseudonym)',p['student_id'],key=f'id_{selected}',disabled=str(selected).startswith('STU-'))
    if new_id!=p['student_id']:
        if new_id and new_id not in st.session_state.profiles:
            # Persist the renamed profile only after the user clicks Save.
            del st.session_state.profiles[selected];p['student_id']=new_id;st.session_state.profiles[new_id]=p;st.session_state.selected=new_id;st.rerun()
        else:st.error('Student ID must be unique and nonempty.')
    p['notes']=st.text_area('Additional scheduling preferences / comments (review and encode into constraints)',p.get('notes',''),height=95,key=f'notes_{selected}')
    st.info('Comments are preserved but are not automatically interpreted as scheduling rules. Encode requirements using the Unavailable and activity fields.')
    next_step('2 🏫 School')

with school_tab:
    st.subheader('Step 2: School schedule')
    st.write('Answer a few questions to enter usual school hours once. You can add different hours for individual days and weekends.')
    school_by_day = {r['day']: r for r in p['school']}
    school_state_key = f'school_setup_{selected}'
    if school_state_key not in st.session_state:
        # Preserve saved/imported records until the parent explicitly applies changes.
        has_existing = any(r.get('enabled') for r in p['school'])
        st.session_state[school_state_key] = 'Review existing days' if has_existing else 'Monday to Friday'
    if st.session_state.pop(f'school_applied_{selected}', False):
        st.success('School schedule applied to this profile. Continue to Sleep, and click Save to SQLite to keep it for future sessions.')
    setup = st.radio('Which school attendance pattern applies?',
        ['Monday to Friday', 'Choose individual days', 'Review existing days'],
        key=school_state_key, horizontal=True,
        help='Choose Review existing days to edit a previously saved or imported schedule without replacing it.')

    if setup == 'Review existing days':
        st.caption('Edit each existing school day directly. Changes take effect in this profile immediately; click Save to SQLite to keep them after restarting.')
        for r in p['school']:
            day = r['day']
            r['enabled'] = st.checkbox(f'{day} — attends school', value=bool(r.get('enabled', False)), key=f'school_review_enabled_{selected}_{day}')
            if r['enabled']:
                c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
                with c1: r['start'] = time_picker('Start time', r.get('start', ''), f'school_review_start_{selected}_{day}')
                with c2: r['end'] = time_picker('Finish time', r.get('end', ''), f'school_review_end_{selected}_{day}')
                with c3: r['travel_before'] = st.number_input('Travel before (min)', min_value=0, max_value=180, step=5, value=int(r.get('travel_before', 0)), key=f'school_review_before_{selected}_{day}')
                with c4: r['travel_after'] = st.number_input('Travel after (min)', min_value=0, max_value=180, step=5, value=int(r.get('travel_after', 0)), key=f'school_review_after_{selected}_{day}')
    else:
        weekdays = DAYS[:5]
        if setup == 'Monday to Friday':
            st.write('**1. Does the student attend school every Monday to Friday?**')
            attends = st.radio('Monday–Friday attendance', ['Yes', 'No'], horizontal=True,
                               key=f'school_attends_{selected}', label_visibility='collapsed')
            selected_weekdays = weekdays if attends == 'Yes' else st.multiselect(
                'Select the weekdays attended', weekdays, key=f'school_weekdays_{selected}')
        else:
            selected_weekdays = st.multiselect('**1. Select school weekdays**', weekdays,
                default=[d for d in weekdays if school_by_day[d].get('enabled')],
                key=f'school_weekdays_custom_{selected}')

        st.write('**2. Are the school hours the same on all selected weekdays?**')
        same_hours = st.radio('Same school hours', ['Yes', 'No'], horizontal=True,
                              key=f'school_same_{selected}', label_visibility='collapsed')
        different_days = []
        if same_hours == 'No' and selected_weekdays:
            different_days = st.multiselect('Which weekdays have different hours?', selected_weekdays,
                key=f'school_exceptions_{selected}')
        usual_days = [d for d in selected_weekdays if d not in different_days]
        draft = {}

        def school_time_fields(prefix, label):
            st.markdown(f'**{label}**')
            c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
            with c1: start = time_picker('Start time', '', f'{prefix}_start')
            with c2: end = time_picker('Finish time', '', f'{prefix}_end')
            with c3: before = st.number_input('Travel before (min)', min_value=0, max_value=180, value=0, step=5, key=f'{prefix}_before')
            with c4: after = st.number_input('Travel after (min)', min_value=0, max_value=180, value=0, step=5, key=f'{prefix}_after')
            return dict(start=start, end=end, travel_before=before, travel_after=after)

        if usual_days:
            usual = school_time_fields(f'school_usual_{selected}',
                '3. Usual school hours — ' + ', '.join(usual_days))
            for day in usual_days: draft[day] = usual.copy()
        for day in different_days:
            draft[day] = school_time_fields(f'school_exception_{selected}_{day}',
                                            f'Different school hours — {day}')

        st.write('**4. Does the student attend school on Saturday or Sunday?**')
        weekends = st.multiselect('Weekend school days (if any)', DAYS[5:],
            key=f'school_weekends_{selected}', placeholder='No weekend school')
        for day in weekends:
            draft[day] = school_time_fields(f'school_weekend_{selected}_{day}', f'{day} school hours')

        if draft:
            st.markdown('**Schedule preview**')
            st.dataframe(pd.DataFrame([
                {'Day': d, 'Start': _time_label(draft[d]['start']),
                 'Finish': _time_label(draft[d]['end']),
                 'Travel before (min)': draft[d]['travel_before'],
                 'Travel after (min)': draft[d]['travel_after']}
                for d in DAYS if d in draft]), hide_index=True, use_container_width=True)
        if st.button('✅ Apply school schedule to student profile', type='primary',
                     key=f'apply_school_{selected}'):
            problems = []
            if not draft:
                problems.append('Select at least one school day.')
            for day, item in draft.items():
                if not item['start'] or not item['end']:
                    problems.append(f'{day}: select both start and finish times.')
                elif minutes(item['end']) <= minutes(item['start']):
                    problems.append(f'{day}: finish time must be later than start time.')
            if problems:
                for msg in problems: st.error(msg)
            else:
                for r in p['school']:
                    day = r['day']
                    r.update(enabled=day in draft)
                    if day in draft: r.update(draft[day])
                    else: r.update(start='', end='', travel_before=0, travel_after=0)
                # Do not mutate a widget's session-state key after it is instantiated.
                # Keep the current setup choice and show a confirmation after rerun.
                st.session_state[f'school_applied_{selected}'] = True
                st.rerun()
        st.caption('The schedule is not changed until you click Apply. This protects existing saved school times.')
    st.caption('Times are displayed in AM/PM and stored as 24-hour HH:MM. Midnight = 12:00 AM.')
    next_step('3 🌙 Sleep')

with sleep_tab:
    st.subheader('Step 3: Sleep schedule')
    st.write('Enter the usual bedtime and **next-morning wake-up time** for weekdays and weekends. Add only days that differ from the usual routine.')
    st.caption('Weekdays mean Monday–Friday bedtimes; weekends mean Saturday–Sunday bedtimes. For example, Friday bedtime belongs to the weekday group, and its wake-up time is Saturday morning.')

    def sleep_time_pair(prefix, heading):
        st.markdown(f'**{heading}**')
        left, right = st.columns(2)
        with left:
            bed = time_picker('Bedtime', '', f'{prefix}_bed_{selected}')
        with right:
            wake = time_picker('Wake up next morning', '', f'{prefix}_wake_{selected}')
        return dict(bedtime=bed, wake=wake)

    weekday_sleep = sleep_time_pair('sleep_usual_weekday', '1. Usual weekday sleep (Monday–Friday)')
    weekend_sleep = sleep_time_pair('sleep_usual_weekend', '2. Usual weekend sleep (Saturday–Sunday)')

    st.markdown('**3. Are there any days with a different bedtime or wake-up time?**')
    exception_days = st.multiselect(
        'Select exceptional days (optional)', DAYS,
        key=f'sleep_exception_days_{selected}',
        help='For example, select Friday if Friday bedtime or Saturday morning wake-up differs from the weekday routine.'
    )
    sleep_exceptions = {}
    for day in DAYS:
        if day in exception_days:
            with st.expander(f'Different sleep times — {day}', expanded=True):
                sleep_exceptions[day] = sleep_time_pair(f'sleep_exception_{day}', f'{day} bedtime and following morning wake-up')

    sleep_draft = {}
    for day in DAYS:
        source = sleep_exceptions.get(day, weekday_sleep if day in DAYS[:5] else weekend_sleep)
        sleep_draft[day] = dict(day=day, bedtime=source['bedtime'], wake=source['wake'])

    st.markdown('**4. Review the seven-day sleep schedule**')
    preview = pd.DataFrame([
        dict(Day=r['day'], Bedtime=_time_label(r['bedtime']),
             **{'Wake next morning': _time_label(r['wake'])},
             Source='Exception' if r['day'] in exception_days else ('Weekday' if r['day'] in DAYS[:5] else 'Weekend'))
        for r in sleep_draft.values()
    ])
    st.dataframe(preview, hide_index=True, use_container_width=True)

    if st.button('✅ Apply sleep schedule to student profile', key=f'apply_sleep_{selected}'):
        missing = [day for day, r in sleep_draft.items() if not r['bedtime'] or not r['wake']]
        invalid = []
        for day, r in sleep_draft.items():
            if not r['bedtime'] or not r['wake']:
                continue
            duration = (minutes(r['wake']) - minutes(r['bedtime'])) % 1440
            if not 240 <= duration <= 900:
                invalid.append(f'{day}: {duration / 60:g} hours')
        if missing:
            st.error('Select bedtime and wake-up time for: ' + ', '.join(missing))
        elif invalid:
            st.error('Please review unusual sleep durations: ' + '; '.join(invalid))
        else:
            p['sleep'] = [sleep_draft[day].copy() for day in DAYS]
            st.session_state[f'sleep_applied_{selected}'] = True
            st.rerun()
    if st.session_state.get(f'sleep_applied_{selected}'):
        st.success('Sleep schedule applied to the student profile. Remember to save the profile to SQLite when finished.')
    st.caption('The existing saved sleep schedule is not changed until you click Apply. Midnight = 12:00 AM.')
    next_step('4 📌 Fixed + travel')

with fixed_tab:
    st.write('**Optional:** No activities are added automatically. Click **Add fixed activity** only for commitments such as tuition or sport. Travel is blocked separately, including 15 or 45 minutes.')
    for i,r in enumerate(list(p['fixed'])):
        with st.expander(f"{i+1}. {r['name']}",expanded=False):
            c1,c2,c3=st.columns(3)
            r['name']=c1.text_input('Activity',r['name'],key=f'fn_{selected}_{i}')
            
            with c2: r['start']=time_picker('Start time',r.get('start',''),key=f'fs_{selected}_{i}')
            
            with c3: r['end']=time_picker('Finish time',r.get('end',''),key=f'fe_{selected}_{i}')
            r['days']=st.multiselect('Days',DAYS,default=r.get('days',[]),key=f'fd_{selected}_{i}')
            c1,c2,c3=st.columns(3)
            r['mode']=c1.selectbox('Mode',['In person','Online'],index=0 if r.get('mode','In person')!='Online' else 1,key=f'fm_{selected}_{i}')
            r['travel_before']=c2.number_input('Travel before (min)',0,240,int(r.get('travel_before',0)),5,key=f'fb_{selected}_{i}')
            r['travel_after']=c3.number_input('Travel after (min)',0,240,int(r.get('travel_after',0)),5,key=f'fa_{selected}_{i}')
            if st.button('Remove activity',key=f'fr_{selected}_{i}'):
                p['fixed'].pop(i);st.rerun()
    if st.button('➕ Add fixed activity'):
        p['fixed'].append(dict(name='',days=[],start='',end='',travel_before=0,travel_after=0,mode='In person'));st.rerun()

    next_step('5 ⭐ Flexible activities')

with flex_tab:
    st.write('**Optional:** Click **Add flexible activity** to enter homework, reading, exercise, recreation or another activity. Nothing is prefilled. **Priority-Driven:** High → Medium → Low; **Baseline:** entry order.')
    for i,r in enumerate(list(p['flexible'])):
        with st.expander(f"{i+1}. {r['name']} · {r['priority']}",expanded=False):
            c1,c2=st.columns(2)
            r['name']=c1.text_input('Activity',r['name'],key=f'an_{selected}_{i}')
            opts=['High','Medium','Low','Not applicable']
            r['priority']=c2.selectbox('Priority',opts,index=opts.index(r.get('priority','Medium')) if r.get('priority') in opts else 1,key=f'ap_{selected}_{i}')
            c1,c2=st.columns(2)
            r['requested_minutes']=c1.number_input('Requested minutes per week',15,3000,int(r.get('requested_minutes',60)),15,key=f'ar_{selected}_{i}')
            r['min_session_minutes']=c2.number_input('Minimum session minutes',15,240,int(r.get('min_session_minutes',30)),15,key=f'am_{selected}_{i}')
            r['days']=st.multiselect('Eligible days',DAYS,default=r.get('days',[]),key=f'ad_{selected}_{i}')
            st.markdown('**Daily distribution preference**')
            current_mode = r.get('distribution_mode', 'Algorithm decides')
            # Ask the parent first; do not assume they want equal distribution.
            equal_choice = st.radio(
                'Would you like to distribute this activity equally across the selected days?',
                ['No', 'Yes'],
                index=1 if current_mode == 'Equal across selected days' else 0,
                horizontal=True, key=f'equal_choice_{selected}_{i}')
            if equal_choice == 'Yes':
                r['distribution_mode'] = 'Equal across selected days'
            else:
                alternative = st.radio(
                    'If not equally, how should the activity be distributed?',
                    ['Let the algorithm decide', 'Set different minutes for each day'],
                    index=1 if current_mode == 'Custom minutes per day' else 0,
                    horizontal=True, key=f'dist_alternative_{selected}_{i}')
                r['distribution_mode'] = ('Custom minutes per day'
                    if alternative == 'Set different minutes for each day' else 'Algorithm decides')
            chosen_days = [d for d in DAYS if d in r['days']]
            if r['distribution_mode'] == 'Equal across selected days':
                if chosen_days:
                    total = int(r['requested_minutes'])
                    units, leftover = divmod(total // 15, len(chosen_days))
                    allocation = {d: (units + (j < leftover)) * 15
                                  for j, d in enumerate(chosen_days)}
                    r['daily_minutes'] = allocation
                    st.caption('Requested daily targets: ' + ', '.join(
                        f'{d}: {v} min' for d, v in allocation.items()))
                    st.caption('If a daily target cannot fit, its unmet minutes will be reported; '
                               'it will not be silently transferred to another day.')
                else:
                    r['daily_minutes'] = {}
                    st.info('Select eligible days to see the daily targets.')
            elif r['distribution_mode'] == 'Custom minutes per day':
                old = r.get('daily_minutes', {})
                new = {}
                for d in chosen_days:
                    new[d] = st.number_input(f'{d} target (minutes)', min_value=0,
                        max_value=3000, value=int(old.get(d, 0)), step=15,
                        key=f'daily_{selected}_{i}_{d}')
                r['daily_minutes'] = new
                assigned = sum(new.values())
                if assigned != int(r['requested_minutes']):
                    st.warning(f'Daily targets total {assigned} min, but weekly requested '
                               f'duration is {r["requested_minutes"]} min. Please make them equal.')
                else:
                    st.success(f'Daily targets total {assigned} minutes.')
            else:
                r['daily_minutes'] = {}
                st.caption('The scheduler may allocate the weekly duration to any eligible day.')
            c1,c2=st.columns(2)
            
            with c1: r['window_start']=time_picker('Eligible window start',r.get('window_start',''),key=f'as_{selected}_{i}')
            
            with c2: r['window_end']=time_picker('Eligible window end',r.get('window_end',''),key=f'ae_{selected}_{i}')
            if st.button('Remove activity',key=f'ax_{selected}_{i}'):
                p['flexible'].pop(i);st.rerun()
    if st.button('➕ Add flexible activity'):
        p['flexible'].append(dict(name='',priority='Not applicable',requested_minutes=15,min_session_minutes=15,days=[],window_start='',window_end='',distribution_mode='Algorithm decides',daily_minutes={}));st.rerun()
    st.caption('15/45 minutes are retained in metrics; timetable occupancy rounds to 30/60 minutes. Enter each activity’s requested duration, priority, eligible days and time window.')
    next_step('6 🚫 Unavailable')

with blocked_tab:
    for field,title in [('unavailable','Unavailable periods (block all activities)'),('screen_free','Screen-free periods (block gaming/screens only)')]:
        st.subheader(title)
        for i,r in enumerate(list(p.get(field,[]))):
            c1,c2,c3,c4=st.columns([2,2,2,1])
            r['day']=c1.selectbox('Day',DAYS,index=DAYS.index(r['day']),key=f'{field}_d_{selected}_{i}')
            
            with c2: r['start']=time_picker('Start time',r.get('start',''),key=f'{field}_s_{selected}_{i}')
            
            with c3: r['end']=time_picker('End time',r.get('end',''),key=f'{field}_e_{selected}_{i}')
            if c4.button('Remove',key=f'{field}_x_{selected}_{i}'):
                p[field].pop(i);st.rerun()
        if st.button('➕ Add period',key=f'{field}_add_{selected}'):
            p.setdefault(field,[]).append(dict(day='Monday',start='',end=''));st.rerun()

    st.caption('Optional: add periods that should be kept free. Otherwise continue.')
    next_step('7 📊 Generate & compare')

with results_tab:
    st.markdown('### Step 7: Generate and compare')
    st.write('Check your information, then generate the baseline and priority-driven timetables. Save the profile from the sidebar to keep it for future sessions.')
    # Do not validate untouched forms: show a calm completion checklist first.
    profile_ready = bool(p.get('age_group') and p.get('year_level'))
    school_days = [r for r in p.get('school', []) if r.get('enabled')]
    school_ready = bool(school_days) and all(r.get('start') and r.get('end') for r in school_days)
    sleep_rows = p.get('sleep', [])
    sleep_ready = len(sleep_rows) == 7 and all(r.get('bedtime') and r.get('wake') for r in sleep_rows)
    ready = profile_ready and school_ready and sleep_ready

    st.markdown('#### Your information')
    for label, complete, guidance in [
        ('Student profile', profile_ready, 'Choose an age group and school year in Step 1.'),
        ('School schedule', school_ready, 'Choose school days and their start/finish times in Step 2.'),
        ('Sleep schedule', sleep_ready, 'Choose bedtime and wake-up time for all seven days in Step 3.'),
    ]:
        st.write(('✅ ' if complete else '○ ') + label + (' — Complete' if complete else ' — Not completed'))
        if not complete:
            st.caption(guidance)
    st.caption('Fixed activities, flexible activities and unavailable periods are optional. Add only those that apply.')

    errors, warnings = ([], [])
    if ready:
        errors, warnings = validate(p)
        if errors:
            st.error('Please review these entries before generating:')
            for msg in errors:
                st.write('❌ ' + msg)
        if warnings:
            st.warning('Please verify these unusual values:')
            for msg in warnings:
                st.write('⚠️ ' + msg)
    else:
        st.info('Complete the required sections above to enable timetable generation. No timetable has been generated yet.')

    if st.button('🚀 Generate both timetables', type='primary', disabled=not ready or bool(errors)):
        try:
            st.session_state[f'result_{selected}'] = compare(p)
            st.session_state[f'result_snapshot_{selected}'] = json.dumps(p, sort_keys=True, ensure_ascii=False)
        except Exception as exc:
            st.error(f'Scheduling failed: {exc}')
    results=st.session_state.get(f'result_{selected}')
    if results and st.session_state.get(f'result_snapshot_{selected}') != json.dumps(p, sort_keys=True, ensure_ascii=False):
        st.warning('Profile inputs have changed since the last generation. Click Generate both timetables again to see the updated schedule.')
        results = None
    if results:
        baseline,priority=results
        if st.button('💾 Save both generated timetables to SQLite'):
            save_results(p, baseline, priority)
            st.success('Both timetable results saved with a snapshot of the student profile.')
        st.caption('Results are generated from the most recent button press. If you change inputs, click Generate again.')
        metrics=pd.DataFrame([baseline['metrics'],priority['metrics']]).set_index('algorithm')
        st.subheader('Algorithm comparison')
        st.dataframe(metrics,use_container_width=True)
        c1,c2,c3=st.columns(3)
        c1.metric('Priority-weighted improvement',f"{(priority['metrics']['priority_satisfaction_pct']-baseline['metrics']['priority_satisfaction_pct']):+.2f} pp" if priority['metrics']['priority_satisfaction_pct'] is not None and baseline['metrics']['priority_satisfaction_pct'] is not None else 'N/A')
        c2.metric('High-priority improvement',f"{(priority['metrics']['high_priority_satisfaction_pct']-baseline['metrics']['high_priority_satisfaction_pct']):+.2f} pp" if priority['metrics']['high_priority_satisfaction_pct'] is not None and baseline['metrics']['high_priority_satisfaction_pct'] is not None else 'N/A')
        c3.metric('Fixed overlaps',priority['metrics']['fixed_conflicts'])
        if priority['metrics']['fixed_conflicts']:
            st.error('Fixed-commitment overlaps were detected. Review these before treating the timetable as feasible.')
        if warnings:st.warning('This schedule contains unverified input warnings and should be treated as provisional.')
        view=st.radio('Timetable view',['Priority-Driven','Baseline'],horizontal=True)
        res=priority if view=='Priority-Driven' else baseline
        st.markdown('<span class="legend" style="background:#bfdbfe">School</span><span class="legend" style="background:#ddd6fe">Sleep</span><span class="legend" style="background:#fed7aa">Fixed</span><span class="legend" style="background:#fde68a">Travel</span><span class="legend" style="background:#fecaca">High</span><span class="legend" style="background:#fef08a">Medium</span><span class="legend" style="background:#bbf7d0">Low</span>',unsafe_allow_html=True)
        compact = st.checkbox('Compact timetable (fit more of the week on one screen)', value=True, key=f'compact_{selected}')
        st.caption('Compact view uses smaller cells and text. The underlying 30-minute slots and activity durations are unchanged.')
        grid = res['timetable']
        cells = {(r.day, r.start): r for r in grid.itertuples()}
        # HTML rowspan merges adjacent identical slots vertically within a day.
        # The 30-minute scheduling grid itself remains unchanged.
        spans = {}
        for d in DAYS:
            k = 0
            while k < 48:
                first = cells[(d, hhmm(k * 30))]
                signature = (first.activity, first.category, first.priority)
                finish = k + 1
                while finish < 48:
                    nxt = cells[(d, hhmm(finish * 30))]
                    if (nxt.activity, nxt.category, nxt.priority) != signature:
                        break
                    finish += 1
                spans[(d, k)] = finish - k
                k = finish
        rows = ['<div style="overflow-x:auto"><table class="calendar">'
                '<thead><tr><th>Time slot</th>' + ''.join(
                    '<th>' + html.escape(d) + '</th>' for d in DAYS) +
                '</tr></thead><tbody>']
        for k in range(48):
            start_time = hhmm(k * 30)
            end_time = hhmm((k + 1) * 30)
            rows.append('<tr><td class="time">' + start_time + '–' + end_time + '</td>')
            for d in DAYS:
                span = spans.get((d, k))
                if span is None:
                    continue  # This row is covered by an earlier rowspan cell.
                r = cells[(d, start_time)]
                kind = r.priority if r.category == 'Flexible' else r.category
                kind = kind if kind in ('School','Sleep','Travel','Fixed',
                    'Unavailable','High','Medium','Low') else 'free'
                label = html.escape(str(r.activity))
                if label:
                    block_end = hhmm((k + span) * 30)
                    duration = span * 30
                    if compact and span <= 1:
                        block_label = f'<strong>{label}</strong>'
                    elif compact:
                        block_label = (f'<strong>{label}</strong><div class="block-time">'
                            f'{start_time}–{block_end}</div>')
                    else:
                        block_label = (f'<strong>{label}</strong><div class="block-time">'
                            f'{start_time}–{block_end} · {duration} min</div>')
                else:
                    block_label = ''
                rows.append(f'<td rowspan="{span}" class="{kind}" '
                            f'title="{label}">{block_label}</td>')
            rows.append('</tr>')
        rows.append('</tbody></table></div>')
        calendar_html = ''.join(rows)
        # In the compact view, 48 rows at 12px each fit many desktop displays.
        # Keep a separate full-size, printable HTML export independent of Streamlit.
        if compact:
            st.markdown('<style>.calendar td{height:12px;padding:1px 2px;line-height:1.05}.calendar{font-size:9px}.calendar th{padding:4px}.calendar .block-time{font-size:8px;margin-top:1px}</style>', unsafe_allow_html=True)
        else:
            st.markdown('<style>.calendar td{height:20px;padding:3px;line-height:1.3}.calendar{font-size:11px}.calendar th{padding:8px}.calendar .block-time{font-size:10px;margin-top:3px}</style>', unsafe_allow_html=True)
        st.markdown(calendar_html, unsafe_allow_html=True)
        printable_css = '''
        @page {size: A4 landscape; margin: 8mm;}
        body{font-family:Arial,sans-serif;color:#17263d;margin:10px}
        h2{margin:0 0 8px;font-size:16px}
        .calendar{width:100%;border-collapse:collapse;table-layout:fixed;font-size:9px}
        .calendar th{background:#17263d;color:white;padding:4px;border:1px solid #cbd5e1}
        .calendar td{border:1px solid #e2e8f0;height:12px;padding:1px 2px;text-align:center;line-height:1.05}
        .calendar .time{background:#f1f5f9;width:75px;text-align:left}
        .calendar .block-time{font-size:8px;opacity:.85}
        .calendar .free{background:white}.calendar .School{background:#bfdbfe}
        .calendar .Sleep{background:#ddd6fe}.calendar .Travel{background:#fde68a}
        .calendar .Fixed{background:#fed7aa}.calendar .Unavailable{background:#e5e7eb}
        .calendar .High{background:#fecaca}.calendar .Medium{background:#fef08a}
        .calendar .Low{background:#bbf7d0}
        @media print{body{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
        '''
        export_html = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
                       '<title>COMP902 Weekly Timetable</title><style>' + printable_css +
                       '</style></head><body><h2>Weekly Timetable — ' +
                       html.escape(view) + ' — ' + html.escape(str(selected)) +
                       '</h2>' + calendar_html + '</body></html>')
        st.download_button('⬇️ Download coloured timetable (HTML — printable as PDF)',
                           data=export_html.encode('utf-8'),
                           file_name=f'{selected}_{view.replace("-", "_")}_weekly_timetable.html',
                           mime='text/html', key=f'html_export_{selected}_{view}')
        st.caption('To save as PDF: open the downloaded HTML in your browser, press Ctrl+P, choose Save as PDF, Landscape, and enable background graphics.')
        st.subheader('Activity allocation breakdown')
        st.dataframe(res['allocations'],use_container_width=True,hide_index=True)
        if len(res['daily_allocations']):
            st.subheader('Daily activity targets and unmet minutes')
            st.dataframe(res['daily_allocations'],use_container_width=True,hide_index=True)
        if len(res['allocations']):
            st.bar_chart(res['allocations'].set_index('activity')[['requested_minutes','scheduled_minutes']])
        st.subheader('Fixed conflicts and validation')
        if len(res['conflicts']):st.dataframe(res['conflicts'],use_container_width=True,hide_index=True)
        else:st.success('No exact-minute fixed overlaps detected.')
        st.subheader('Download results')
        st.download_button('⬇️ Timetable CSV',res['timetable'].to_csv(index=False),file_name=f'{selected}_{view}_timetable.csv',mime='text/csv')
        st.download_button('⬇️ Allocation CSV',res['allocations'].to_csv(index=False),file_name=f'{selected}_{view}_allocations.csv',mime='text/csv')
        if len(res['daily_allocations']):
            st.download_button('⬇️ Daily allocation CSV',res['daily_allocations'].to_csv(index=False),file_name=f'{selected}_{view}_daily_allocations.csv',mime='text/csv')
        zbuf=io.BytesIO()
        with zipfile.ZipFile(zbuf,'w',zipfile.ZIP_DEFLATED) as z:
            for name,item in [('baseline',baseline),('priority',priority)]:
                for key in ('timetable','allocations','conflicts'):
                    z.writestr(f'{selected}_{name}_{key}.csv',item[key].to_csv(index=False))
                z.writestr(f'{selected}_{name}_metrics.json',json.dumps(item['metrics'],indent=2))
            z.writestr(f'{selected}_profile.json',json.dumps(p,indent=2,ensure_ascii=False))
        st.download_button('📦 Download complete comparison ZIP',zbuf.getvalue(),file_name=f'{selected}_comparison.zip',mime='application/zip')
    else:st.info('Review your profile, then click **Generate both timetables**.')

st.divider()
st.caption('Research note: The scheduler uses a conservative 30-minute occupancy grid. Fifteen-minute commitments block any touched slot; allocation minutes are capped at requested duration. A priority score alone does not prove improved wellbeing or learning outcomes. Keep real responses separate from synthetic tests.')
