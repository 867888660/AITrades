const API = {
    async request(url, options = {}) {
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json'
            }
        };

        if (options.body && typeof options.body === 'object') {
            options.body = JSON.stringify(options.body);
        }

        const response = await fetch(`/api${url}`, { ...defaultOptions, ...options });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || error.message || 'API Request Failed');
        }

        return response.json();
    },

    get(url) {
        return this.request(url, { method: 'GET' });
    },

    post(url, data) {
        return this.request(url, { method: 'POST', body: data });
    },

    put(url, data) {
        return this.request(url, { method: 'PUT', body: data });
    },

    delete(url) {
        return this.request(url, { method: 'DELETE' });
    }
};
