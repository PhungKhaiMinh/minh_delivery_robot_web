/**
 * MQTT cho Admin — bridge mode (mặc định):
 *   Trình duyệt → WebSocket tới FastAPI → paho TCP 1883 → broker.
 *   Không cần broker hỗ trợ WebSocket.
 */
(function (global) {
    function randomSuffix() {
        return Math.random().toString(36).slice(2, 10);
    }

    function connectViaBridge(bridgePath, onConnState) {
        var proto = global.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var path = bridgePath || '/api/admin/mqtt-bridge';
        var url = proto + '//' + global.location.host + path;
        var msgListeners = [];
        var ws = new WebSocket(url);
        var connected = false;

        function report(state, detail) {
            try { onConnState(state, detail || ''); } catch (_) {}
        }

        ws.onopen = function () {
            report('reconnecting', 'Đang kết nối MQTT qua máy chủ…');
        };
        ws.onclose = function () {
            connected = false;
            report('disconnected', 'WebSocket đóng');
        };
        ws.onerror = function () {
            connected = false;
            report('error', 'WebSocket lỗi');
        };
        ws.onmessage = function (ev) {
            var d;
            try { d = JSON.parse(ev.data); } catch (_) { return; }

            if (d.type === 'connack') {
                if (d.ok) {
                    connected = true;
                    report('connected');
                } else {
                    report('error', 'Broker từ chối (rc=' + d.rc + ')');
                }
                return;
            }
            if (d.type === 'msg') {
                var buf = { toString: function () { return String(d.p); } };
                for (var i = 0; i < msgListeners.length; i++) {
                    try { msgListeners[i](d.t, buf); } catch (_) {}
                }
                return;
            }
            if (d.type === 'disconnected') {
                connected = false;
                report('disconnected', 'Broker ngắt kết nối');
                return;
            }
            if (d.type === 'error') {
                report('error', d.message || 'Lỗi bridge');
            }
        };

        return {
            subscribe: function (topic, opts) {
                if (ws.readyState !== WebSocket.OPEN) return;
                var qos = (opts && opts.qos) || 0;
                ws.send(JSON.stringify({ type: 'sub', topics: [topic], qos: qos }));
            },
            publish: function (topic, payload, opts, cb) {
                if (ws.readyState !== WebSocket.OPEN) {
                    if (cb) cb(new Error('ws closed'));
                    return;
                }
                var qos = (opts && opts.qos) || 0;
                ws.send(JSON.stringify({ type: 'pub', topic: topic, payload: payload, qos: qos }));
                if (cb) cb(null);
            },
            on: function (evt, fn) {
                if (evt === 'message') msgListeners.push(fn);
            },
            isConnected: function () { return connected; },
        };
    }

    function connect(wsUrl, clientIdPrefix, onConnState, auth) {
        if (global.BOOKBOT_MQTT_MODE === 'bridge') {
            return connectViaBridge(global.BOOKBOT_MQTT_BRIDGE_PATH, onConnState);
        }
        if (typeof mqtt === 'undefined') {
            onConnState('error', 'mqtt.js chưa được tải');
            return null;
        }
        var url = (wsUrl != null ? String(wsUrl).trim() : '');
        if (!url) {
            onConnState(
                'error',
                'Thiếu MQTT_WS_URL trên server — với direct mode cần biến môi trường MQTT_WS_URL=wss://... hoặc bật bridge (MQTT_USE_SERVER_BRIDGE=true).'
            );
            return null;
        }
        if (global.location.protocol === 'https:' && url.indexOf('ws://') === 0) {
            onConnState(
                'error',
                'Trang HTTPS không dùng được ws:// — đặt MQTT_WS_URL dạng wss://... hoặc bật MQTT_USE_SERVER_BRIDGE=true (bridge qua cùng host).'
            );
            return null;
        }
        var clientId = (clientIdPrefix || 'bookbot') + '-' + randomSuffix();
        auth = auth || {};
        var opts = {
            clientId: clientId,
            reconnectPeriod: 4000,
            connectTimeout: 15000,
            protocolVersion: 4,
            clean: true,
        };
        if (auth.username) opts.username = auth.username;
        if (auth.password != null && String(auth.password) !== '') {
            opts.password = String(auth.password);
        }
        var client = mqtt.connect(url, opts);
        client.on('connect', function () { onConnState('connected'); });
        client.on('reconnect', function () { onConnState('reconnecting'); });
        client.on('close', function () { onConnState('disconnected'); });
        client.on('offline', function () { onConnState('disconnected'); });
        client.on('error', function (err) { onConnState('error', (err && err.message) || 'Lỗi MQTT'); });
        return client;
    }

    function safeParseJson(str) {
        try { return JSON.parse(str); } catch (_) { return null; }
    }

    global.AdminMqtt = {
        connect: connect,
        connectViaBridge: connectViaBridge,
        safeParseJson: safeParseJson,
        randomSuffix: randomSuffix,
    };
})(window);
