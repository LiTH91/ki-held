import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Navbar from './components/Navbar';
import UrlInput from './components/UrlInput';
import LoginGuide from './components/LoginGuide';
import ScanStatus from './components/ScanStatus';
import CommentReview from './components/CommentReview';
import ApiTest from './components/ApiTest';

function App() {
  return (
    <Router>
      <div className="min-h-screen bg-gray-100">
        <Navbar />
        <main className="container mx-auto px-4 py-8">
          <Routes>
            <Route path="/" element={<UrlInput />} />
            <Route path="/login-guide" element={<LoginGuide />} />
            <Route path="/scan-status" element={<ScanStatus />} />
            <Route path="/review/:scanId" element={<CommentReview />} />
            <Route path="/api-test" element={<ApiTest />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}

export default App;