const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const multer = require('multer');
const path = require('path');
const http = require('http');
const socketIo = require('socket.io');
const fs = require('fs');
const axios = require('axios');
const pdf = require('pdf-parse');
require('dotenv').config();

const app = express();
const server = http.createServer(app);
const io = socketIo(server, {
    cors: { origin: "*", methods: ["GET", "POST"] },
    pingTimeout: 60000,
    pingInterval: 25000
});

const PORT = process.env.PORT || 5000;

app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));
app.use(express.static('.'));

// MongoDB connection with improved options
const MONGODB_URI = process.env.MONGODB_URI;

if (!MONGODB_URI || MONGODB_URI.includes('atlas-sql')) {
    console.error('ERROR: MONGODB_URI noto\'g\'ri formatda yoki mavjud emas!');
    console.error('Iltimos, MongoDB Atlasdan "Standard Connection String" (mongodb+srv://...) ni oling.');
}

mongoose.connect(MONGODB_URI, {
    useNewUrlParser: true,
    useUnifiedTopology: true,
    serverSelectionTimeoutMS: 10000, // Timeoutni 10 soniyaga oshirdim
    socketTimeoutMS: 45000,
})
    .then(() => console.log('✅ MongoDB muvaffaqiyatli ulandi'))
    .catch(err => {
        console.error('❌ MongoDB ulanish xatosi!');
        console.error('Xato tafsiloti:', err.message);
        console.log('Eslatma: MongoDB Atlasda IP limitingizni (Network Access) 0.0.0.0/0 qilib o\'zgartiring.');
    });

// Health check endpoint
app.get('/api/health', (req, res) => {
    res.json({
        status: 'ok',
        database: mongoose.connection.readyState === 1 ? 'connected' : 'disconnected',
        time: new Date()
    });
});

// Schemas
const userSchema = new mongoose.Schema({
    firstName: { type: String, required: true },
    lastName: { type: String, required: true },
    phone: { type: String, unique: true, required: true },
    password: { type: String, required: true },
    groupCode: { type: String, required: true },
    role: { type: String, default: 'student', enum: ['student', 'admin'] },
    createdAt: { type: Date, default: Date.now }
});

const testSchema = new mongoose.Schema({
    title: { type: String, required: true },
    description: String,
    questions: { type: Array, required: true },
    timeLimit: { type: Number, default: 30 },
    totalScore: { type: Number, default: 100 },
    startTime: { type: Date, default: Date.now },
    endTime: { type: Date, default: () => new Date(Date.now() + 7 * 24 * 60 * 60 * 1000) },
    groupCode: { type: String, required: true },
    createdBy: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
    createdAt: { type: Date, default: Date.now }
});

const resultSchema = new mongoose.Schema({
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
    testId: { type: mongoose.Schema.Types.ObjectId, ref: 'Test' },
    answers: Array,
    score: Number,
    totalScore: Number,
    percentage: Number,
    passed: Boolean,
    grade: { type: Number, default: 0 },
    timeTaken: Number,
    submittedAt: { type: Date, default: Date.now }
});

const activitySchema = new mongoose.Schema({
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User' },
    userName: String,
    activity: String,
    testTitle: String,
    score: Number,
    timestamp: { type: Date, default: Date.now }
});

const User = mongoose.model('User', userSchema);
const Test = mongoose.model('Test', testSchema);
const Result = mongoose.model('Result', resultSchema);
const Activity = mongoose.model('Activity', activitySchema);

// Multer for file uploads
const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        const dir = './uploads';
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
        cb(null, dir);
    },
    filename: (req, file, cb) => {
        const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1E9);
        cb(null, uniqueSuffix + '-' + file.originalname);
    }
});
const upload = multer({ storage });

// Helpers
async function logActivity(userId, activity, details = {}) {
    try {
        const user = await User.findById(userId);
        const log = new Activity({
            userId,
            userName: user ? `${user.firstName} ${user.lastName}` : 'System',
            activity,
            ...details
        });
        await log.save();
        io.emit('activity_update', log);
    } catch (e) {
        console.error('Logging xatosi:', e);
    }
}

// Auth Endpoints
app.post('/api/login', async (req, res) => {
    try {
        const { phone, password } = req.body;

        const ADMIN_PHONE = process.env.ADMIN_PHONE || 'admin';
        const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || 'admin123';

        let user;
        if (phone === ADMIN_PHONE && password === ADMIN_PASSWORD) {
            user = await User.findOne({ phone: ADMIN_PHONE });
            if (!user) {
                user = new User({
                    firstName: 'Admin',
                    lastName: 'System',
                    phone: ADMIN_PHONE,
                    password: ADMIN_PASSWORD,
                    role: 'admin',
                    groupCode: 'ADMIN'
                });
                await user.save();
            }
        } else {
            user = await User.findOne({ phone, password });
        }

        if (!user) {
            return res.status(401).json({ success: false, error: 'Telefon raqam yoki parol noto\'g\'ri' });
        }

        await logActivity(user._id, user.role === 'admin' ? 'admin_login' : 'student_login');

        res.json({
            success: true,
            user: {
                id: user._id,
                firstName: user.firstName,
                lastName: user.lastName,
                phone: user.phone,
                role: user.role,
                groupCode: user.groupCode
            }
        });
    } catch (e) {
        res.status(500).json({ success: false, error: 'Serverda xatolik yuz berdi' });
    }
});

app.post('/api/register', async (req, res) => {
    try {
        const { firstName, lastName, phone, groupCode, password } = req.body;
        const existing = await User.findOne({ phone });
        if (existing) return res.json({ success: false, error: 'Bu telefon raqam allaqachon ro\'yxatdan o\'tgan' });

        const user = new User({ firstName, lastName, phone, groupCode, password });
        await user.save();
        await logActivity(user._id, 'registration');
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Student Endpoints
app.get('/api/tests/student/:groupCode', async (req, res) => {
    try {
        const tests = await Test.find({ groupCode: req.params.groupCode }).lean().sort({ createdAt: -1 });
        const userId = req.headers['user-id'];

        for (let test of tests) {
            const result = await Result.findOne({ userId, testId: test._id });
            test.isTaken = !!result;
        }

        res.json({ success: true, tests });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.get('/api/tests/take/:testId', async (req, res) => {
    try {
        const test = await Test.findById(req.params.testId);
        if (!test) return res.status(404).json({ success: false, error: 'Test topilmadi' });
        res.json({ success: true, test });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.post('/api/tests/submit', async (req, res) => {
    try {
        const { testId, answers, timeTaken } = req.body;
        const userId = req.headers['user-id'];
        const test = await Test.findById(testId);

        if (!test) return res.status(404).json({ success: false, error: 'Test topilmadi' });

        let score = 0;
        const processedAnswers = [];

        test.questions.forEach((q, i) => {
            const isCorrect = q.correctAnswer === answers[i];
            const qScore = q.score || 5;
            if (isCorrect) score += qScore;
            processedAnswers.push({
                question: q.text,
                selected: answers[i],
                correct: q.correctAnswer,
                isCorrect,
                score: isCorrect ? qScore : 0
            });
        });

        const totalScore = test.totalScore || 100;
        const percentage = Math.round((score / totalScore) * 100);
        const passed = percentage >= 60;

        // Calculate Grade (0-5)
        let grade = 0;
        if (percentage >= 90) grade = 5;
        else if (percentage >= 80) grade = 4;
        else if (percentage >= 70) grade = 3;
        else if (percentage >= 60) grade = 2;
        else if (percentage >= 40) grade = 1;
        else grade = 0;

        const result = new Result({
            userId, testId, answers: processedAnswers,
            score, totalScore, percentage, passed, grade, timeTaken
        });
        await result.save();

        await logActivity(userId, 'test_completed', { testTitle: test.title, score: percentage, grade });

        res.json({ success: true, score, totalScore, percentage, passed, grade });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Admin Endpoints
app.get('/api/admin/dashboard', async (req, res) => {
    try {
        const stats = {
            students: await User.countDocuments({ role: 'student' }),
            tests: await Test.countDocuments(),
            results: await Result.countDocuments(),
            files: fs.existsSync('./uploads') ? fs.readdirSync('./uploads').length : 0,
            todayResults: await Result.countDocuments({ submittedAt: { $gte: new Date().setHours(0, 0, 0, 0) } })
        };
        const recentActivities = await Activity.find().sort({ timestamp: -1 }).limit(10);
        res.json({ success: true, stats, recentActivities });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.post('/api/admin/tests/create', async (req, res) => {
    try {
        const { title, description, questions, timeLimit, groupCode, startTime, endTime } = req.body;
        const totalScore = questions.reduce((sum, q) => sum + (q.score || 5), 0);

        const test = new Test({
            title, description, questions, timeLimit, totalScore, groupCode,
            startTime: startTime || new Date(),
            endTime: endTime || new Date(Date.now() + 86400000 * 7),
            createdBy: req.headers['user-id']
        });

        await test.save();
        const activityType = questions.some(q => q.createdByAI) ? 'test_created_from_ai' : 'test_created_manual';
        await logActivity(req.headers['user-id'], activityType, { testTitle: title });
        res.json({ success: true, testId: test._id });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.get('/api/admin/tests', async (req, res) => {
    try {
        const tests = await Test.find().sort({ createdAt: -1 });
        res.json({ success: true, tests });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.delete('/api/admin/tests/:id', async (req, res) => {
    try {
        await Test.findByIdAndDelete(req.params.id);
        await Result.deleteMany({ testId: req.params.id });
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.get('/api/admin/students', async (req, res) => {
    try {
        const students = await User.find({ role: 'student' }).sort({ firstName: 1 });
        res.json({ success: true, students });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.get('/api/admin/results', async (req, res) => {
    try {
        const results = await Result.find().populate('userId').populate('testId').sort({ submittedAt: -1 });
        res.json({ success: true, results });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.get('/api/admin/activities', async (req, res) => {
    try {
        const activities = await Activity.find().sort({ timestamp: -1 }).limit(50);
        res.json({ success: true, activities });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// Admin Management Endpoints
app.get('/api/admin/list', async (req, res) => {
    try {
        const admins = await User.find({ role: 'admin' }).sort({ createdAt: -1 });
        res.json({ success: true, admins });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.post('/api/admin/create', async (req, res) => {
    try {
        const { firstName, lastName, phone, password } = req.body;
        const existing = await User.findOne({ phone });
        if (existing) return res.json({ success: false, error: 'Bu login (telefon) band' });

        const admin = new User({ firstName, lastName, phone, password, role: 'admin', groupCode: 'ADMIN' });
        await admin.save();
        await logActivity(req.headers['user-id'], 'admin_created', { userName: `${firstName} ${lastName}` });
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

app.delete('/api/admin/delete/:id', async (req, res) => {
    try {
        const adminCount = await User.countDocuments({ role: 'admin' });
        if (adminCount <= 1) return res.json({ success: false, error: 'Oxirgi adminni o\'chirib bo\'lmaydi' });

        await User.findByIdAndDelete(req.params.id);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// AI Generation
app.post('/api/admin/ai/generate', async (req, res) => {
    try {
        const { topic, count, level, sourceText } = req.body;
        const apiKey = process.env.DEEPSEEK_API_KEY;

        if (!apiKey) return res.status(500).json({ success: false, error: 'DeepSeek API Key topilmadi' });

        const prompt = `Siz professional o'qituvchisiz. Quyidagi mavzu/matn asosida ${count} ta savol yarating.
Mavzu: ${topic}
Matn: ${sourceText || 'Umumiy bilimlar'}
Daraja: ${level}
Javob faqat JSON formatida bo'lsin: 
{ "questions": [ { "text": "Savol?", "options": ["A", "B", "C", "D"], "correctAnswer": "A", "score": 5, "createdByAI": true } ] }`;

        const response = await axios.post('https://api.deepseek.com/chat/completions', {
            model: "deepseek-chat",
            messages: [
                { role: "system", content: "Siz faqat JSON qaytaradigan yordamchisiz." },
                { role: "user", content: prompt }
            ],
            response_format: { type: 'json_object' }
        }, {
            headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' }
        });

        const data = typeof response.data.choices[0].message.content === 'string'
            ? JSON.parse(response.data.choices[0].message.content)
            : response.data.choices[0].message.content;

        res.json({ success: true, questions: data.questions || data });
    } catch (e) {
        console.error('AI Error:', e.message);
        res.json({ success: false, error: 'AI generatsiyada xatolik: ' + e.message });
    }
});

app.post('/api/admin/ai/parse-pdf', upload.single('file'), async (req, res) => {
    try {
        if (!req.file) throw new Error('Fayl yuklanmadi');
        const dataBuffer = fs.readFileSync(req.file.path);
        const data = await pdf(dataBuffer);
        fs.unlinkSync(req.file.path);
        res.json({ success: true, text: data.text });
    } catch (e) {
        res.json({ success: false, error: e.message });
    }
});

// Socket logic
const onlineUsers = new Map();

io.on('connection', (socket) => {
    const { userId, userName, role, groupCode } = socket.handshake.query;
    if (userId) {
        onlineUsers.set(userId, { id: userId, name: userName, role, groupCode, status: 'online', socketId: socket.id });
        io.emit('online_students', Array.from(onlineUsers.values()));
    }

    socket.on('test_started', (data) => {
        if (onlineUsers.has(data.studentId)) {
            const user = onlineUsers.get(data.studentId);
            user.status = 'testing';
            user.currentTest = data.testTitle;
            io.emit('student_status_update', { ...data, status: 'testing' });
            io.emit('online_students', Array.from(onlineUsers.values()));
        }
    });

    socket.on('screen_update', (data) => {
        io.emit('screen_mirror_update', data);
    });

    socket.on('test_submitted', (data) => {
        if (onlineUsers.has(data.studentId)) {
            const user = onlineUsers.get(data.studentId);
            user.status = 'online';
            delete user.currentTest;
            io.emit('student_status_update', { ...data, status: 'finished' });
            io.emit('test_submission', data);
            io.emit('online_students', Array.from(onlineUsers.values()));
        }
    });

    socket.on('disconnect', () => {
        if (userId) {
            onlineUsers.delete(userId);
            io.emit('online_students', Array.from(onlineUsers.values()));
        }
    });
});

if (require.main === module) {
    server.listen(PORT, () => {
        console.log(`🚀 Server http://localhost:${PORT} portida ishga tushdi`);
    });
}

module.exports = app;
