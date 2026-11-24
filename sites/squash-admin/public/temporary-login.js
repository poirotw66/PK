(function () {
    'use strict';
    var password = document.getElementById('password');
    var toggle = document.getElementById('show-password');
    if (!password || !toggle) return;
    toggle.onchange = function () {
        password.type = toggle.checked ? 'text' : 'password';
    };
}());
