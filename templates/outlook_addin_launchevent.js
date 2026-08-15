var CHECK_URL = {{ check_url|tojson }};
var SEND_TIMEOUT_MS = 8000;

function onMessageSendHandler(event) {
  try {
    var item = Office.context.mailbox.item;
    var token = Office.context.roamingSettings.get("gudanesToken");
    if (!token) {
      // Not set up for this mailbox yet -- fail open, never block on a missing token.
      event.completed({ allowEvent: true });
      return;
    }
    item.to.getAsync(function (toResult) {
      var recipientEmail = "";
      if (toResult.status === Office.AsyncResultStatus.Succeeded && toResult.value && toResult.value.length) {
        recipientEmail = toResult.value[0].emailAddress || "";
      }
      item.subject.getAsync(function (subjectResult) {
        var subject = subjectResult.status === Office.AsyncResultStatus.Succeeded ? (subjectResult.value || "") : "";
        item.body.getAsync("text", function (bodyResult) {
          var body = bodyResult.status === Office.AsyncResultStatus.Succeeded ? (bodyResult.value || "") : "";
          checkConflict(token, recipientEmail, subject, body, event);
        });
      });
    });
  } catch (e) {
    // Any unexpected error reading the draft -- fail open rather than block send.
    event.completed({ allowEvent: true });
  }
}

function checkConflict(token, recipientEmail, subject, body, event) {
  var params = new URLSearchParams({ token: token, recipient_email: recipientEmail, subject: subject, body: body });
  var settled = false;

  var timer = setTimeout(function () {
    if (settled) return;
    settled = true;
    event.completed({ allowEvent: true }); // fail open on timeout
  }, SEND_TIMEOUT_MS);

  fetch(CHECK_URL, { method: "POST", body: params })
    .then(function (resp) {
      if (settled) return null;
      if (!resp.ok) { throw new Error("check-send-conflict returned " + resp.status); }
      return resp.json();
    })
    .then(function (data) {
      if (settled || data === null) return;
      settled = true;
      clearTimeout(timer);
      if (data && data.conflict) {
        event.completed({
          allowEvent: false,
          errorMessage: data.note || "This message may not match current pricing or availability. Please double-check before sending.",
        });
      } else {
        event.completed({ allowEvent: true });
      }
    })
    .catch(function () {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      event.completed({ allowEvent: true }); // fail open on any network/parse error
    });
}

// Required so Outlook can find this handler -- without it, SoftBlock treats
// the add-in as broken and blocks every send, which is the opposite of the
// point. See "Handle OnMessageSend and OnAppointmentSend events" in
// Microsoft's Office Add-ins docs.
Office.actions.associate("onMessageSendHandler", onMessageSendHandler);
