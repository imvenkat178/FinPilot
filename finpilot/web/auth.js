import { S, $, esc, icon } from './core.js';
let mode = 'login', hooks, signingIn = false;

function showForm(message='') {
  document.body.dataset.auth='guest';
  const screen=$('auth-screen');
  screen.hidden=false;
  const register=mode==='register';
  screen.innerHTML=`<div class="auth-brand"><span class="brand-mark">F</span>FinPilot</div><div class="auth-layout"><div class="auth-intro"><span class="eyebrow">YOUR MONEY, IN FOCUS</span><h1>A clearer view.<br>A better plan.</h1><p>Bring your accounts, paychecks and priorities together in one private workspace.</p><div class="auth-highlights"><span>${icon('wallet')}Every account, organized</span><span>${icon('split')}A plan for every paycheck</span><span>${icon('spark')}AI grounded in your numbers</span></div><span class="auth-note">You control your data. Payments use a simulation provider.</span></div><section class="auth-card" aria-labelledby="auth-title"><h2 id="auth-title">${register?'Create your workspace':'Welcome back'}</h2><p>${register?'Your finances start with you.':'Sign in to pick up where you left off.'}</p><form id="auth-form" class="stack-form">${register?'<label>Your name<input name="name" autocomplete="name" maxlength="100" required></label>':''}<label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required></label><label>Password<input name="password" type="password" autocomplete="${register?'new-password':'current-password'}" minlength="${register?'12':'1'}" maxlength="128" required></label>${register?'<span class="auth-help">At least 12 characters.</span><label class="checkbox-line"><input type="checkbox" name="sample_data">Start with sample data to explore</label>':''}<div class="form-result error" role="alert">${esc(message)}</div><button type="submit" class="button primary">${register?'Create workspace':'Sign in'} ${icon('arrow')}</button></form><div class="auth-switch">${register?'Already have an account?':'New to FinPilot?'} <button type="button" class="text-button" data-auth-mode="${register?'login':'register'}">${register?'Sign in':'Create an account'}</button></div></section></div>`;
}

async function establish(session) {
  S.session=session;
  document.body.dataset.auth='ready';
  $('auth-screen').hidden=true;
  await hooks.onAuthenticated();
}

export async function signOut() {
  try {
    const response=await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':S.session?.csrf_token || ''}});
    if (!response.ok && response.status!==401) throw new Error('Sign out could not be completed. Please retry.');
    S.session=null;
    hooks.onSignedOut?.();
    showForm();
    $('auth-screen').querySelector('input')?.focus();
  } catch(error) {
    hooks.onError?.(error.message || 'Sign out could not be completed. Please retry.');
  }
}

export async function initializeAuth(config) {
  hooks=config;
  document.addEventListener('click',e=>{
    const button=e.target.closest('[data-auth-mode]');
    if(button && !signingIn){mode=button.dataset.authMode;showForm();$('auth-screen').querySelector('input')?.focus();}
  });
  document.addEventListener('finpilot-session-expired',()=>{
    if(!S.session)return;
    S.session=null;hooks.onSignedOut?.();showForm('Your session has expired. Please sign in again.');
  });
  document.addEventListener('submit',async e=>{
    if(e.target.id!=='auth-form')return;
    e.preventDefault();
    if(signingIn)return;
    const form=e.target,button=form.querySelector('button[type="submit"]');
    const values=Object.fromEntries(new FormData(form));
    if(mode==='register')values.sample_data=form.elements.sample_data.checked;
    signingIn=true;button.disabled=true;form.querySelector('.form-result').textContent='';
    try{
      const response=await fetch('/api/auth/'+mode,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
      const data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Check the fields and try again.');
      await establish(data);
    }catch(error){form.querySelector('.form-result').textContent=error.message;}
    finally{signingIn=false;if(button.isConnected)button.disabled=false;}
  });
  try{
    const response=await fetch('/api/auth/me',{credentials:'same-origin'});
    if(response.ok)await establish(await response.json());
    else if(response.status===401)showForm();
    else showForm('The service is unavailable. Please try signing in again.');
  }catch{showForm('Could not reach FinPilot. Check your connection and try again.');}
}
