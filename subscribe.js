// functions/api/subscribe.js
//
// Cloudflare Pages Function — runs on Cloudflare's servers, not in the visitor's browser.
// This is where your Resend API key actually lives, safely out of sight.
//
// SETUP (updated — Resend simplified this, you no longer need an Audience/Segment):
// 1. In Resend, create an API key: Dashboard -> API Keys -> Create API Key.
//    Copy it (starts with "re_").
// 2. In your Cloudflare Pages project settings -> Environment Variables, add:
//      RESEND_API_KEY = your secret key from Resend
//    Do this in the dashboard UI — never commit this value into your code or git repo.
// 3. Deploy this file at: functions/api/subscribe.js (same folder structure as your site).
//    Cloudflare Pages automatically turns this into a working POST /api/subscribe endpoint.
//
// Contacts created this way show up under Resend -> Contacts. You can optionally group
// them into a Segment later from the dashboard, but it's no longer required to save them.

export async function onRequestPost(context) {
  const { request, env } = context;

  let email;
  try {
    const body = await request.json();
    email = (body.email || '').trim();
  } catch (e) {
    return new Response(JSON.stringify({ error: 'Invalid request body' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  // Basic email validation
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return new Response(JSON.stringify({ error: 'Invalid email address' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  if (!env.RESEND_API_KEY) {
    return new Response(JSON.stringify({ error: 'Server is not configured yet' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  try {
    const resendRes = await fetch('https://api.resend.com/contacts', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.RESEND_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        email: email,
        unsubscribed: false
      })
    });

    if (!resendRes.ok) {
      const errText = await resendRes.text();
      // A duplicate contact isn't really a failure from the user's point of view
      if (resendRes.status === 409) {
        return new Response(JSON.stringify({ ok: true, note: 'already subscribed' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      console.error('Resend error:', errText);
      return new Response(JSON.stringify({ error: 'Could not subscribe right now' }), {
        status: 502,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (err) {
    console.error('Subscribe function error:', err);
    return new Response(JSON.stringify({ error: 'Server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}
